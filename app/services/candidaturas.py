"""Admissão de alunos novos, da pré-inscrição às boas-vindas, pela ordem do procedimento do colégio:

 1. a família submete a pré-inscrição (Google Forms, ou a secretaria regista-a / importa o CSV das respostas);
 2. a candidatura vai para a coordenação do nível da classe pretendida (alerta ao coordenador do nível);
 3. a coordenação verifica a vaga e convoca a família para a reunião obrigatória; regista a reunião e, se houve,
    a entrevista ou avaliação do aluno;
 4. a coordenação valida ou rejeita;
 5. a Direcção autoriza e com isso informa os serviços administrativos (alerta à secretaria);
 6. a secretaria comunica a vaga à família, com o link para formalizar a matrícula;
 7. a família preenche os dados e carrega os documentos nesse link (sem conta);
 8. a secretaria valida: o aluno, o encarregado, os documentos e a matrícula são criados de uma vez (sem transcrever
    para outras folhas);
 9. o aluno fica na turma, visível à coordenação pedagógica (alerta "novo aluno" para organizar a turma);
10. boas-vindas: welcome kit, documentação e integração; a família recebe a mensagem de boas-vindas.

Cada passo fica no histórico da candidatura (quem, quando, nota).
"""
import logging
import re
import secrets
import unicodedata
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import armazenamento
from app.common import ano_activo, as_dict, numero_processo
from app.config import get_settings
from app.models import (
    Alerta, Aluno, AlunoEncarregado, AnoLectivo, Candidatura, CandidaturaDocumento, Classe, DocumentoAluno,
    Encarregado, Estabelecimento, Matricula, Mensagem, NivelEnsino, Turma, Utilizador,
)
from app.security import Contexto
from app.services import comunicacao
from app.services import turmas as ocupacao_turmas

log = logging.getLogger("clacs.candidaturas")

NOMES_ESTADO = {
    "submetida": "Pré-inscrição recebida", "reuniao_marcada": "Reunião marcada", "reuniao_realizada": "Reunião realizada",
    "validada": "Validada pela coordenação", "autorizada": "Autorizada pela Direcção",
    "vaga_comunicada": "Vaga comunicada à família", "documentos_entregues": "Dados e documentos entregues",
    "matriculada": "Matriculado", "concluida": "Integração concluída", "rejeitada": "Rejeitada", "cancelada": "Cancelada",
}
# Documentos pedidos à família na formalização (tipo, nome, obrigatório).
DOCUMENTOS = [
    ("cedula_bi_aluno", "Cédula ou BI do aluno", True),
    ("fotografia", "Fotografia tipo passe do aluno", True),
    ("bi_encarregado", "BI do encarregado de educação", True),
    ("boletim_vacinas", "Boletim de vacinas", False),
    ("declaracao_escola_anterior", "Declaração ou certificado da escola anterior", False),
    ("outro", "Outro documento", False),
]
CAMPOS_OBRIGATORIOS = {"data_nascimento": "data de nascimento", "sexo": "sexo", "morada": "morada",
                       "contacto_emergencia": "contacto de emergência", "telefone_emergencia": "telefone de emergência",
                       "bi_encarregado": "BI do encarregado"}
MAX_DOCUMENTOS = 15

# Quem faz cada etapa (a Direcção e o administrador podem fazer todas).
PAPEIS = {"coordenacao": ({"coordenacao"}, "coordenação"), "direccao": (set(), "Direcção"),
          "secretaria": ({"secretaria"}, "secretaria")}


# ---------------------------------------------------------------- utilitários
def _agora() -> datetime:
    return datetime.now(timezone.utc)


def _hora_local(dt: datetime) -> str:
    """Data e hora no fuso da escola (as horas chegam do browser com o fuso de quem marcou)."""
    if dt.tzinfo is not None:
        dt = dt.astimezone(ZoneInfo(get_settings().fuso_horario))
    return dt.strftime("%d/%m/%Y às %H:%M")


def _registar(c: Candidatura, estado: str, por: Utilizador | None, nota: str | None = None) -> None:
    c.estado = estado
    c.historico = [*(c.historico or []), {"em": _agora().isoformat(timespec="seconds"), "estado": estado,
                                          "por": por.nome if por else None, "nota": nota}]


def _nota(c: Candidatura, texto: str) -> None:
    """Acrescenta ao histórico sem mudar de etapa (ex.: mensagens enviadas)."""
    c.historico = [*(c.historico or []), {"em": _agora().isoformat(timespec="seconds"), "estado": c.estado,
                                          "por": None, "nota": texto}]


def exigir_papel(db: Session, ctx: Contexto, c: Candidatura, papel: str) -> None:
    perfis, nome = PAPEIS[papel]
    if not ctx.perfis & ({"admin", "direccao"} | perfis):
        raise HTTPException(403, f"Esta etapa é da {nome}")
    # Um coordenador só decide as candidaturas do(s) nível(eis) que coordena (se o nível tiver coordenador).
    if papel == "coordenacao" and not ctx.perfis & {"admin", "direccao"} and c.nivel_id:
        nivel = db.get(NivelEnsino, c.nivel_id)
        if nivel and nivel.coordenador_id and nivel.coordenador_id != ctx.id:
            raise HTTPException(403, f"Esta candidatura é da coordenação do {nivel.nome}")


def exigir_estado(c: Candidatura, *estados: str) -> None:
    if c.estado not in estados:
        esperado = " ou ".join(NOMES_ESTADO[e].lower() for e in estados)
        raise HTTPException(409, f"A candidatura está em «{NOMES_ESTADO[c.estado]}»; este passo exige: {esperado}")


def _definir_classe(db: Session, c: Candidatura, classe_id: int | None) -> None:
    c.classe_id, c.nivel_id = classe_id, None
    if classe_id is not None:
        classe = db.get(Classe, classe_id)
        nivel = classe and db.get(NivelEnsino, classe.nivel_id)
        if nivel is None or nivel.estabelecimento_id != c.estabelecimento_id:
            raise HTTPException(422, f"A classe {classe_id} não existe")
        c.nivel_id = nivel.id


def _definir_ano(db: Session, c: Candidatura, ano_id: int | None) -> None:
    if ano_id is None:
        ano = ano_activo(db, c.estabelecimento_id, obrigatorio=False)
        c.ano_id = ano.id if ano else None
        return
    ano = db.get(AnoLectivo, ano_id)
    if ano is None or ano.estabelecimento_id != c.estabelecimento_id or ano.estado == "encerrado":
        raise HTTPException(422, f"O ano lectivo {ano_id} não existe ou está encerrado")
    c.ano_id = ano.id


def link_formalizacao(c: Candidatura) -> str | None:
    return c.token and f"{get_settings().frontend_url.rstrip('/')}/candidatura/{c.token}"


# ---------------------------------------------------------------- avisos (alertas internos e mensagens à família)
PARA = {"coordenacao": "Coordenação", "direccao": "Direcção", "secretaria": "Secretaria", "pedagogico": "Coordenação pedagógica"}


def _alertar(db: Session, c: Candidatura, para: str, mensagem: str, gravidade: str = "baixa") -> None:
    """Fecha os avisos anteriores desta candidatura e abre o da etapa seguinte."""
    for a in db.scalars(select(Alerta).where(Alerta.estabelecimento_id == c.estabelecimento_id,
                                             Alerta.tipo == "candidatura", Alerta.estado.in_(("novo", "visto")))):
        if (a.dados or {}).get("candidatura_id") == c.id:
            a.estado = "tratado"
    db.add(Alerta(estabelecimento_id=c.estabelecimento_id, tipo="candidatura", aluno_id=c.aluno_id, gravidade=gravidade,
                  dados={"candidatura_id": c.id, "para": para, "estado": c.estado},
                  mensagem=f"[{PARA[para]}] {mensagem}"))


def _fechar_alertas(db: Session, c: Candidatura) -> None:
    for a in db.scalars(select(Alerta).where(Alerta.estabelecimento_id == c.estabelecimento_id,
                                             Alerta.tipo == "candidatura", Alerta.estado.in_(("novo", "visto")))):
        if (a.dados or {}).get("candidatura_id") == c.id:
            a.estado = "tratado"


def avisar_familia(db: Session, c: Candidatura, assunto: str, corpo: str) -> None:
    """Depois da matrícula, pela fila normal (app + WhatsApp); antes, directamente por WhatsApp e e-mail."""
    escola = db.get(Estabelecimento, c.estabelecimento_id).nome
    texto = f"{escola}: {corpo}"
    if c.encarregado_id:
        for canal in ("app", "whatsapp"):
            db.add(Mensagem(estabelecimento_id=c.estabelecimento_id, encarregado_id=c.encarregado_id, aluno_id=c.aluno_id,
                            canal=canal, assunto=assunto, corpo=texto))
        if c.email:
            _enviar(c, "email", c.email, assunto, texto)
        return
    destinos = [(canal, destino) for canal, destino in (("whatsapp", c.telefone), ("email", c.email)) if destino]
    enviados = [canal for canal, destino in destinos if _enviar(c, canal, destino, f"{escola} — {assunto}", texto)]
    if enviados:
        _nota(c, f"Mensagem «{assunto}» enviada por {', '.join(enviados)}")
    else:
        _nota(c, f"Mensagem «{assunto}» não enviada: " + ("o envio falhou; contacte a família"
                                                          if destinos else "a família não tem telefone nem e-mail"))


def _enviar(c: Candidatura, canal: str, destino: str, assunto: str, texto: str) -> bool:
    try:
        comunicacao.enviar(canal, destino, assunto, texto)
        return True
    except Exception as erro:  # a etapa não falha por causa da mensagem; fica registado no histórico
        log.warning("Candidatura %s: envio por %s falhou: %s", c.id, canal, erro)
        _nota(c, f"Envio por {canal} falhou: {erro}")
        return False


# ---------------------------------------------------------------- 1-2. pré-inscrição
def criar(db: Session, est: int, dados: dict, origem: str, por: Utilizador | None = None,
          id_externo: str | None = None, respostas: dict | None = None) -> Candidatura:
    classe_id, ano_id = dados.pop("classe_id", None), dados.pop("ano_id", None)
    c = Candidatura(estabelecimento_id=est, origem=origem, id_externo=id_externo, respostas=respostas, historico=[],
                    **dados)
    _definir_classe(db, c, classe_id)
    _definir_ano(db, c, ano_id)
    db.add(c)
    db.flush()
    _registar(c, "submetida", por, {"google_forms": "Recebida pelo Google Forms", "importacao": "Importada do CSV das "
                                    "respostas do formulário"}.get(origem, "Registada pela secretaria"))
    nivel = c.nivel_id and db.get(NivelEnsino, c.nivel_id)
    coordenador = nivel and nivel.coordenador_id and db.get(Utilizador, nivel.coordenador_id)
    destino = f" ({coordenador.nome})" if coordenador else ""
    classe = c.classe_id and db.get(Classe, c.classe_id)
    _alertar(db, c, "coordenacao", f"Nova pré-inscrição: {c.nome_aluno}, "
                                   f"{classe.designacao if classe else 'classe por indicar'}{destino}. "
                                   "Verifique a vaga e convoque a família.")
    return c


def actualizar(db: Session, c: Candidatura, dados: dict) -> None:
    if c.estado in ("matriculada", "concluida", "rejeitada", "cancelada"):
        raise HTTPException(409, f"A candidatura está «{NOMES_ESTADO[c.estado]}»: já não se altera")
    muda_classe = "classe_id" in dados and dados["classe_id"] != c.classe_id
    muda_ano = "ano_id" in dados and dados["ano_id"] != c.ano_id
    # Depois de validada, a classe e o ano já sustentam a decisão da coordenação e da Direcção: não se mudam.
    if c.estado == "validada" and (muda_classe or muda_ano):
        raise HTTPException(409, "A candidatura já foi validada: a classe e o ano lectivo não se alteram")
    anterior = c.classe_id
    if "classe_id" in dados:
        _definir_classe(db, c, dados.pop("classe_id"))
    if "ano_id" in dados:
        _definir_ano(db, c, dados.pop("ano_id"))
    for k, v in dados.items():
        setattr(c, k, v)
    if muda_classe:
        classe = c.classe_id and db.get(Classe, c.classe_id)
        antes = db.get(Classe, anterior).designacao if anterior else "classe por indicar"
        _nota(c, f"Classe alterada de {antes} para {classe.designacao if classe else 'classe por indicar'}")
        # A coordenação do novo nível passa a ser quem decide: recebe o aviso (o anterior fica tratado).
        _alertar(db, c, "coordenacao", f"Candidatura de {c.nome_aluno} alterada para "
                                        f"{classe.designacao if classe else 'classe por indicar'}: verifique a vaga e "
                                        "convoque a família.")


# ---------------------------------------------------------------- 3-4. coordenação
def vagas(db: Session, c: Candidatura) -> dict:
    """Vagas da classe pretendida no ano da candidatura, turma a turma."""
    if not c.classe_id or not c.ano_id:
        return {"capacidade": 0, "ocupados": 0, "livres": 0, "turmas": []}
    turmas = [ocupacao_turmas.vagas(db, t) for t in db.scalars(
        select(Turma).where(Turma.estabelecimento_id == c.estabelecimento_id, Turma.ano_id == c.ano_id,
                            Turma.classe_id == c.classe_id).order_by(Turma.designacao))]
    return {"capacidade": sum(t["capacidade"] for t in turmas), "ocupados": sum(t["alunos"] for t in turmas),
            "livres": sum(t["vagas"] for t in turmas), "turmas": turmas}


def marcar_reuniao(db: Session, c: Candidatura, por: Utilizador, reuniao_em: datetime, local: str | None,
                   mensagem: str | None) -> None:
    exigir_estado(c, "submetida", "reuniao_marcada")
    remarcada = c.estado == "reuniao_marcada"
    c.reuniao_em, c.reuniao_local = reuniao_em, local or "Secretaria do colégio"
    quando = _hora_local(reuniao_em)
    _registar(c, "reuniao_marcada", por, f"{'Reunião remarcada' if remarcada else 'Reunião marcada'} para {quando}")
    corpo = (f"A candidatura de {c.nome_aluno} está em análise. Convocamos a família para uma reunião obrigatória "
             f"no dia {quando} ({c.reuniao_local}).")
    avisar_familia(db, c, "Reunião de candidatura", f"{corpo} {mensagem}" if mensagem else corpo)


def registar_reuniao(db: Session, c: Candidatura, por: Utilizador, avaliacao: str) -> None:
    exigir_estado(c, "reuniao_marcada", "reuniao_realizada")
    c.avaliacao = avaliacao
    _registar(c, "reuniao_realizada", por, "Reunião realizada; avaliação registada")


def validar(db: Session, c: Candidatura, por: Utilizador, parecer: str | None) -> None:
    exigir_estado(c, "reuniao_realizada")
    if not c.classe_id:
        raise HTTPException(409, "Indique a classe pretendida antes de validar")
    if not c.ano_id:
        raise HTTPException(409, "Indique o ano lectivo da candidatura antes de validar")
    v = vagas(db, c)
    if v["livres"] <= 0:
        classe = db.get(Classe, c.classe_id).designacao
        raise HTTPException(409, f"Não há vagas na {classe} ({v['ocupados']}/{v['capacidade']}): "
                                 "crie uma turma ou rejeite a candidatura")
    c.parecer = parecer
    _registar(c, "validada", por, parecer or "Validada pela coordenação")
    _alertar(db, c, "direccao", f"Candidatura de {c.nome_aluno} validada pela coordenação: aguarda a decisão da Direcção.")


def rejeitar(db: Session, c: Candidatura, por: Utilizador, motivo: str | None) -> None:
    exigir_estado(c, "submetida", "reuniao_marcada", "reuniao_realizada", "validada")
    if not (motivo or "").strip():
        raise HTTPException(422, "Indique o motivo da rejeição (parecer)")
    c.parecer = motivo
    _registar(c, "rejeitada", por, motivo)
    _fechar_alertas(db, c)
    avisar_familia(db, c, "Candidatura", f"Lamentamos informar que a candidatura de {c.nome_aluno} não foi aceite. "
                                         f"{motivo}")


def cancelar(db: Session, c: Candidatura, por: Utilizador, motivo: str | None) -> None:
    """Desistência da família (ou candidatura duplicada)."""
    if c.estado in ("matriculada", "concluida", "rejeitada", "cancelada"):
        raise HTTPException(409, f"A candidatura está «{NOMES_ESTADO[c.estado]}»: já não se cancela")
    _registar(c, "cancelada", por, motivo or "Cancelada")
    _fechar_alertas(db, c)


# ---------------------------------------------------------------- 5-6. Direcção e secretaria
def autorizar(db: Session, c: Candidatura, por: Utilizador, nota: str | None) -> None:
    exigir_estado(c, "validada")
    _registar(c, "autorizada", por, nota or "Autorizada pela Direcção; serviços administrativos informados")
    _alertar(db, c, "secretaria", f"A Direcção autorizou a admissão de {c.nome_aluno}: comunique a vaga à família e "
                                  "envie o link para formalizar a matrícula.", gravidade="media")


def comunicar_vaga(db: Session, c: Candidatura, por: Utilizador, mensagem: str | None) -> None:
    exigir_estado(c, "autorizada", "vaga_comunicada")
    c.token = c.token or secrets.token_urlsafe(24)
    classe = db.get(Classe, c.classe_id).designacao
    _registar(c, "vaga_comunicada", por, "Vaga e passos da matrícula comunicados à família")
    corpo = (f"Temos o prazer de informar que foi atribuída uma vaga a {c.nome_aluno} na {classe}. Para formalizar a "
             f"matrícula: 1) preencha os dados do aluno e do encarregado; 2) carregue os documentos (cédula ou BI do "
             f"aluno, fotografia, BI do encarregado, boletim de vacinas); 3) aguarde a confirmação da secretaria. "
             f"Link: {link_formalizacao(c)}")
    avisar_familia(db, c, "Vaga atribuída", f"{corpo} {mensagem}" if mensagem else corpo)
    _fechar_alertas(db, c)


# ---------------------------------------------------------------- 7. formalização pela família (link com token)
def por_token(db: Session, token: str) -> Candidatura:
    c = db.scalar(select(Candidatura).where(Candidatura.token == token)) if len(token) >= 20 else None
    if c is None:
        raise HTTPException(404, "Link inválido ou expirado. Contacte a secretaria do colégio.")
    return c


def _pode_editar(c: Candidatura) -> None:
    if c.estado != "vaga_comunicada":
        raise HTTPException(409, "Os dados já foram entregues à secretaria" if c.estado == "documentos_entregues"
                            else "Esta candidatura já não aceita alterações")


def gravar_dados(c: Candidatura, dados: dict) -> None:
    _pode_editar(c)
    c.dados_matricula = {**(c.dados_matricula or {}), **dados}


def anexar(db: Session, c: Candidatura, tipo: str, conteudo: bytes, mime: str | None, nome: str | None) -> CandidaturaDocumento:
    _pode_editar(c)
    if tipo not in {t for t, _, _ in DOCUMENTOS}:
        raise HTTPException(422, f"Tipo de documento desconhecido: {tipo}")
    if len(documentos(db, c)) >= MAX_DOCUMENTOS:
        raise HTTPException(409, f"Máximo de {MAX_DOCUMENTOS} documentos por candidatura")
    chave = armazenamento.guardar(c.estabelecimento_id, "candidaturas", conteudo, mime, nome)
    d = CandidaturaDocumento(candidatura_id=c.id, tipo=tipo, nome=nome, ficheiro_url=armazenamento.url(chave))
    db.add(d)
    db.flush()
    return d


def remover_documento(db: Session, c: Candidatura, doc_id: int) -> None:
    _pode_editar(c)
    d = db.get(CandidaturaDocumento, doc_id)
    if d is None or d.candidatura_id != c.id:
        raise HTTPException(404, "Documento não encontrado")
    db.delete(d)


def em_falta(db: Session, c: Candidatura) -> list[str]:
    dados = c.dados_matricula or {}
    faltam = [nome for campo, nome in CAMPOS_OBRIGATORIOS.items()
              if not dados.get(campo) and not (campo in ("data_nascimento", "sexo") and getattr(c, campo))]
    entregues = {d.tipo for d in documentos(db, c)}
    return faltam + [nome for tipo, nome, obrig in DOCUMENTOS if obrig and tipo not in entregues]


def submeter(db: Session, c: Candidatura) -> None:
    _pode_editar(c)
    faltam = em_falta(db, c)
    if faltam:
        raise HTTPException(422, f"Falta preencher ou entregar: {', '.join(faltam)}")
    c.correccao = None
    _registar(c, "documentos_entregues", None, "Dados e documentos entregues pela família")
    _alertar(db, c, "secretaria", f"A família de {c.nome_aluno} entregou os dados e os documentos: valide e matricule.",
             gravidade="media")


def pedir_correccao(db: Session, c: Candidatura, por: Utilizador, correccao: str) -> None:
    exigir_estado(c, "documentos_entregues")
    c.correccao = correccao
    _registar(c, "vaga_comunicada", por, f"Correcção pedida: {correccao}")
    _fechar_alertas(db, c)
    avisar_familia(db, c, "Matrícula: correcção", f"Falta um passo para concluir a matrícula de {c.nome_aluno}: "
                                                  f"{correccao} Use o mesmo link: {link_formalizacao(c)}")


# ---------------------------------------------------------------- 8-9. matrícula
def _encarregado(db: Session, c: Candidatura, d: dict) -> Encarregado:
    """O encarregado já existente (mesmo BI, e-mail ou telefone: irmãos já na escola) ou um novo."""
    est = c.estabelecimento_id
    email, telefone, bi = d.get("email") or c.email, d.get("telefone") or c.telefone, d.get("bi_encarregado")
    for coluna, valor in ((Encarregado.bi_numero, bi), (Encarregado.email, email), (Encarregado.telefone, telefone)):
        if valor:
            ee = db.scalar(select(Encarregado).where(Encarregado.estabelecimento_id == est, coluna == valor).limit(1))
            if ee:
                ee.nif = ee.nif or d.get("nif")
                ee.bi_numero = ee.bi_numero or bi
                ee.whatsapp = ee.whatsapp or d.get("whatsapp")
                return ee
    ee = Encarregado(estabelecimento_id=est, nome_completo=d.get("nome_encarregado") or c.nome_encarregado, nif=d.get("nif"),
                     bi_numero=bi, telefone=telefone, whatsapp=d.get("whatsapp") or telefone, email=email,
                     profissao=d.get("profissao"), morada=d.get("morada_encarregado") or d.get("morada"))
    db.add(ee)
    db.flush()
    return ee


def matricular(db: Session, c: Candidatura, por: Utilizador, turma_id: int | None) -> None:
    exigir_estado(c, "documentos_entregues")
    if not c.ano_id:
        raise HTTPException(409, "A candidatura não tem ano lectivo")
    if turma_id is None:
        turma = ocupacao_turmas.escolher(db, c.estabelecimento_id, c.ano_id, c.classe_id)
    else:
        turma = db.get(Turma, turma_id)
        if turma is None or turma.estabelecimento_id != c.estabelecimento_id or turma.ano_id != c.ano_id \
                or turma.classe_id != c.classe_id:
            raise HTTPException(422, f"A turma {turma_id} não é da classe e do ano lectivo da candidatura")
        ocupacao_turmas.garantir_vaga(db, turma)
    d = c.dados_matricula or {}
    nascimento = d.get("data_nascimento")
    a = Aluno(estabelecimento_id=c.estabelecimento_id, numero_processo=numero_processo(db, c.estabelecimento_id),
              nome_completo=d.get("nome_aluno") or c.nome_aluno,
              data_nascimento=date.fromisoformat(nascimento) if nascimento else c.data_nascimento,
              sexo=d.get("sexo") or c.sexo, nacionalidade=d.get("nacionalidade") or "Angolana",
              bi_numero=d.get("bi_numero"), morada=d.get("morada"), alergias=d.get("alergias"),
              necessidades_especiais=d.get("necessidades_especiais"), grupo_sanguineo=d.get("grupo_sanguineo"),
              condicoes_saude=d.get("condicoes_saude"), medicacao=d.get("medicacao"),
              contacto_emergencia=d.get("contacto_emergencia"), telefone_emergencia=d.get("telefone_emergencia"),
              escola_anterior=c.escola_anterior)
    db.add(a)
    db.flush()
    ee = _encarregado(db, c, d)
    db.add(AlunoEncarregado(aluno_id=a.id, encarregado_id=ee.id, parentesco=c.parentesco, principal=True,
                            responsavel_financeiro=True))
    for doc in documentos(db, c):
        db.add(DocumentoAluno(aluno_id=a.id, tipo=doc.tipo, ficheiro_url=doc.ficheiro_url))
        if doc.tipo == "fotografia" and not a.foto_url:
            a.foto_url = doc.ficheiro_url
    m = Matricula(estabelecimento_id=c.estabelecimento_id, aluno_id=a.id, ano_id=c.ano_id, turma_id=turma.id, tipo="nova",
                  registado_por=por.id, observacoes=f"Candidatura {c.id}")
    db.add(m)
    db.flush()
    c.aluno_id, c.encarregado_id, c.matricula_id, c.turma_id = a.id, ee.id, m.id, turma.id
    _registar(c, "matriculada", por, f"Matriculado na turma {turma.designacao} (processo {a.numero_processo})")
    _alertar(db, c, "pedagogico", f"Novo aluno {a.nome_completo} na turma {turma.designacao}: dados disponíveis para "
                                  "organizar a turma e preparar o início das aulas. Falta a integração (boas-vindas).")


# ---------------------------------------------------------------- 10. boas-vindas
def boas_vindas(db: Session, c: Candidatura, por: Utilizador, dados: dict) -> None:
    exigir_estado(c, "matriculada", "concluida")
    mensagem = dados.pop("mensagem", None)
    reuniao = dados.get("reuniao_integracao")
    c.integracao = {**(c.integracao or {}), **{k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in dados.items()},
                    "concluida_em": _agora().isoformat(timespec="seconds")}
    primeira = c.estado == "matriculada"
    _registar(c, "concluida", por, "Boas-vindas, documentação e welcome kit" if primeira else "Integração actualizada")
    if primeira:
        _fechar_alertas(db, c)
        turma = db.get(Turma, c.turma_id)
        aluno = db.get(Aluno, c.aluno_id)
        corpo = (f"Bem-vindos! {aluno.nome_completo} está matriculado na turma {turma.designacao} "
                 f"(n.º de processo {aluno.numero_processo}).")
        if reuniao:
            corpo += f" Contamos convosco na integração a {_hora_local(reuniao)}."
        avisar_familia(db, c, "Bem-vindos", f"{corpo} {mensagem}" if mensagem else corpo)


# ---------------------------------------------------------------- Google Forms
def normalizar(texto: str) -> str:
    sem_acentos = unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", sem_acentos.lower()).strip()


# Perguntas do formulário → campos, pela ordem de verificação (o 1.º termo encontrado no título decide).
PERGUNTAS = [
    ("data_nascimento", ("nascimento",)),
    ("sexo", ("sexo", "genero")),
    ("escola_anterior", ("escola anterior", "escola de proveniencia", "escola actual", "escola atual", "proveniencia")),
    ("email", ("e-mail", "email", "correio electronico", "correio eletronico")),
    ("telefone", ("telefone", "telemovel", "whatsapp", "contacto", "contato")),
    ("parentesco", ("parentesco", "grau de", "relacao com")),
    ("classe", ("classe", "ano de escolaridade", "nivel pretendido")),
    ("ano_lectivo", ("ano lectivo", "ano letivo")),
    ("nome_encarregado", ("encarregado", "nome do pai", "nome da mae", "responsavel", "pai/mae", "mae/pai")),
    ("nome_aluno", ("aluno", "crianca", "educando", "candidato", "estudante", "filho")),
    ("observacoes", ("observa", "comentario", "informacao adicional", "informacoes adicionais", "mais alguma")),
]
TITULOS_SUGERIDOS = ["Nome completo do aluno", "Data de nascimento do aluno", "Sexo", "Classe pretendida",
                     "Escola anterior", "Nome do encarregado de educação", "Parentesco", "Telefone / WhatsApp", "E-mail",
                     "Observações"]


def _texto(v) -> str:
    if isinstance(v, list):
        return ", ".join(str(x) for x in v if x not in (None, ""))
    return "" if v is None else str(v).strip()


def _data(v: str) -> date | None:
    for formato in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(v.strip()[:10], formato).date()
        except ValueError:
            continue
    return None


def _sexo(v: str) -> str | None:
    n = normalizar(v)
    if any(x in n for x in ("femin", "menina", "rapariga")) or n == "f":
        return "F"
    if any(x in n for x in ("mascul", "menino", "rapaz")) or n == "m":
        return "M"
    return None


def _parentesco(v: str) -> str:
    n = normalizar(v)
    return "mae" if n.startswith("mae") else "pai" if n.startswith("pai") else "tutor" if "tutor" in n else "outro"


def _chave_classe(texto: str) -> str:
    return re.sub(r"[^a-z0-9]", "", normalizar(texto).replace("classe", ""))


def _classe(db: Session, est: int, v: str) -> Classe | None:
    classes = db.scalars(select(Classe).join(NivelEnsino, NivelEnsino.id == Classe.nivel_id)
                         .where(NivelEnsino.estabelecimento_id == est)).all()
    chave = _chave_classe(v)
    exacta = next((c for c in classes if _chave_classe(c.designacao) == chave), None)
    if exacta or not chave:
        return exacta
    numero = re.match(r"\d+", chave)
    return numero and next((c for c in classes if re.match(rf"{numero.group()}(?!\d)", _chave_classe(c.designacao))), None)


def ler_respostas(db: Session, est: int, respostas: dict) -> tuple[dict, list[str]]:
    """Converte as respostas do formulário (título → valor) nos campos da candidatura; devolve também os avisos."""
    campos: dict = {}
    for titulo, valor in respostas.items():
        texto = _texto(valor)
        if not texto:
            continue
        n = normalizar(titulo)
        campo = next((c for c, termos in PERGUNTAS if any(t in n for t in termos)), None)
        if campo and campo not in campos:
            campos[campo] = texto
    avisos, dados = [], {"observacoes": campos.get("observacoes")}
    for campo in ("nome_aluno", "nome_encarregado", "escola_anterior"):
        dados[campo] = campos.get(campo)
    dados["telefone"] = campos.get("telefone")
    dados["email"] = campos.get("email", "").lower() or None
    if "data_nascimento" in campos:
        dados["data_nascimento"] = _data(campos["data_nascimento"])
        if dados["data_nascimento"] is None:
            avisos.append(f"Data de nascimento não reconhecida: {campos['data_nascimento']}")
    if "sexo" in campos:
        dados["sexo"] = _sexo(campos["sexo"])
    if "parentesco" in campos:
        dados["parentesco"] = _parentesco(campos["parentesco"])
    if "classe" in campos:
        classe = _classe(db, est, campos["classe"])
        dados["classe_id"] = classe.id if classe else None
        if classe is None:
            avisos.append(f"Classe não reconhecida: «{campos['classe']}» (corrija na candidatura)")
    else:
        avisos.append("O formulário não indica a classe pretendida")
    if "ano_lectivo" in campos:
        ano = db.scalar(select(AnoLectivo).where(AnoLectivo.estabelecimento_id == est,
                                                 AnoLectivo.designacao == campos["ano_lectivo"]))
        dados["ano_id"] = ano.id if ano and ano.estado != "encerrado" else None
    faltam = [nome for campo, nome in (("nome_aluno", "nome do aluno"), ("nome_encarregado", "nome do encarregado"))
              if not dados.get(campo)]
    if not dados["telefone"] and not dados["email"]:
        faltam.append("telefone ou e-mail")
    if faltam:
        raise HTTPException(422, f"Resposta sem {', '.join(faltam)}. Perguntas recebidas: {', '.join(respostas)}")
    return {k: v for k, v in dados.items() if v is not None}, avisos


def receber_formulario(db: Session, est: int, respostas: dict, id_externo: str | None,
                       origem: str = "google_forms") -> tuple[Candidatura, bool, list[str]]:
    """Cria a candidatura de uma resposta do formulário. Reenviar a mesma resposta não a duplica."""
    if id_externo:
        existente = db.scalar(select(Candidatura).where(Candidatura.estabelecimento_id == est,
                                                        Candidatura.id_externo == id_externo))
        if existente:
            return existente, True, []
    dados, avisos = ler_respostas(db, est, respostas)
    # A mesma criança com o mesmo contacto, ainda em curso: é a mesma pré-inscrição (CSV depois do script, 2.º envio…)
    nome = normalizar(dados["nome_aluno"])
    for c in db.scalars(select(Candidatura).where(Candidatura.estabelecimento_id == est,
                                                  Candidatura.estado.not_in(("rejeitada", "cancelada")))):
        mesmo_contacto = (dados.get("email") and c.email == dados["email"]) or \
            (dados.get("telefone") and c.telefone == dados["telefone"])
        if mesmo_contacto and normalizar(c.nome_aluno) == nome:
            return c, True, []
    c = criar(db, est, dados, origem, id_externo=id_externo, respostas={k: _texto(v) for k, v in respostas.items()})
    for aviso in avisos:
        _nota(c, aviso)
    return c, False, avisos


# ---------------------------------------------------------------- leitura
def documentos(db: Session, c: Candidatura) -> list[CandidaturaDocumento]:
    return list(db.scalars(select(CandidaturaDocumento).where(CandidaturaDocumento.candidatura_id == c.id)
                           .order_by(CandidaturaDocumento.id)))


def resumo(db: Session, c: Candidatura) -> dict:
    classe = c.classe_id and db.get(Classe, c.classe_id)
    nivel = c.nivel_id and db.get(NivelEnsino, c.nivel_id)
    turma = c.turma_id and db.get(Turma, c.turma_id)
    ano = c.ano_id and db.get(AnoLectivo, c.ano_id)
    return {**{k: v for k, v in as_dict(c).items() if k != "token"},
            "classe": classe.designacao if classe else None, "nivel": nivel.nome if nivel else None,
            "ano": ano.designacao if ano else None, "turma": turma.designacao if turma else None,
            "estado_nome": NOMES_ESTADO[c.estado], "documentos": len(documentos(db, c)),
            "link_formalizacao": link_formalizacao(c)}


def detalhe(db: Session, c: Candidatura) -> dict:
    nivel = c.nivel_id and db.get(NivelEnsino, c.nivel_id)
    coordenador = nivel and nivel.coordenador_id and db.get(Utilizador, nivel.coordenador_id)
    return {**resumo(db, c), "lista_documentos": [as_dict(d) for d in documentos(db, c)], "vagas": vagas(db, c),
            "coordenador": coordenador.nome if coordenador else None, "em_falta": em_falta(db, c),
            "documentos_pedidos": documentos_pedidos()}


def documentos_pedidos() -> list[dict]:
    return [{"tipo": t, "nome": n, "obrigatorio": o} for t, n, o in DOCUMENTOS]


def publica(db: Session, c: Candidatura) -> dict:
    """O que a família vê no link: sem notas internas da coordenação."""
    classe = c.classe_id and db.get(Classe, c.classe_id)
    ano = c.ano_id and db.get(AnoLectivo, c.ano_id)
    return {"escola": db.get(Estabelecimento, c.estabelecimento_id).nome, "nome_aluno": c.nome_aluno,
            "data_nascimento": c.data_nascimento, "sexo": c.sexo, "nome_encarregado": c.nome_encarregado,
            "telefone": c.telefone, "email": c.email, "classe": classe.designacao if classe else None,
            "ano": ano.designacao if ano else None, "estado": c.estado, "estado_nome": NOMES_ESTADO[c.estado],
            "pode_editar": c.estado == "vaga_comunicada", "correccao": c.correccao, "dados": c.dados_matricula or {},
            "documentos": [{"id": d.id, "tipo": d.tipo, "nome": d.nome, "criado_em": d.criado_em} for d in documentos(db, c)],
            "documentos_pedidos": documentos_pedidos(), "em_falta": em_falta(db, c)}
