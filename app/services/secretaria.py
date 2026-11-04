"""Secretaria: documentos dos alunos (exigidos, em falta, validação), modelos de declaração e a ficha que a família
confirma na renovação da matrícula."""
import re
import string
from datetime import date, datetime, timezone

from fastapi import HTTPException
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, Spacer
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app import pdf
from app.common import ano_activo
from app.models import (
    Aluno, AlunoEncarregado, AnoLectivo, Classe, DocumentoAluno, DocumentoExigido, Encarregado, Estabelecimento,
    Matricula, Mensagem, ModeloDeclaracao, Turma,
)

DOCUMENTOS_PADRAO = [("cedula_bi_aluno", "Cédula ou BI do aluno", True), ("fotografia", "Fotografia tipo passe", True),
                     ("bi_encarregado", "BI do encarregado de educação", True),
                     ("boletim_vacinas", "Boletim de vacinas", True),
                     ("declaracao_escola_anterior", "Declaração ou certificado da escola anterior", False)]
MODELOS_PADRAO = [
    ("matricula", "Declaração de matrícula",
     "Para os devidos efeitos, declara-se que <b>{nome}</b>, nascido(a) a {data_nascimento}, com o processo n.º "
     "{processo}, está matriculado(a) neste estabelecimento de ensino na <b>{classe}</b>, turma <b>{turma}</b>, no ano "
     "lectivo <b>{ano}</b>."),
    ("frequencia", "Declaração de frequência",
     "Para os devidos efeitos, declara-se que <b>{nome}</b>, nascido(a) a {data_nascimento}, com o processo n.º "
     "{processo}, frequenta com regularidade a <b>{classe}</b>, turma <b>{turma}</b>, no ano lectivo <b>{ano}</b>, "
     "sendo seu encarregado de educação {encarregado}."),
]
VARIAVEIS = {"nome": "Nome do aluno", "processo": "N.º de processo", "data_nascimento": "Data de nascimento",
             "bi": "BI ou cédula do aluno", "classe": "Classe", "turma": "Turma", "ano": "Ano lectivo",
             "encarregado": "Encarregado de educação", "escola": "Nome da escola", "data": "Data de hoje"}


def preparar(db: Session, est: int) -> None:
    """Documentos exigidos e modelos de declaração iniciais de uma escola nova."""
    db.add_all([DocumentoExigido(estabelecimento_id=est, tipo=t, nome=n, obrigatorio=o) for t, n, o in DOCUMENTOS_PADRAO])
    db.add_all([ModeloDeclaracao(estabelecimento_id=est, codigo=c, nome=n, texto=t) for c, n, t in MODELOS_PADRAO])


# ---------------------------------------------------------------- documentos
def exigidos(db: Session, est: int, classe_id: int | None) -> list[DocumentoExigido]:
    """Documentos pedidos a um aluno da classe: os de todas as classes e os dessa classe."""
    return list(db.scalars(select(DocumentoExigido).where(
        DocumentoExigido.estabelecimento_id == est, DocumentoExigido.activo,
        or_(DocumentoExigido.classe_id.is_(None), DocumentoExigido.classe_id == classe_id)).order_by(DocumentoExigido.id)))


def _turma_actual(db: Session, est: int, aluno_id: int) -> Turma | None:
    ano = ano_activo(db, est, obrigatorio=False)
    m = ano and db.scalar(select(Matricula).where(Matricula.aluno_id == aluno_id, Matricula.ano_id == ano.id,
                                                  Matricula.estado == "activa"))
    return db.get(Turma, m.turma_id) if m else None


def situacao(db: Session, a: Aluno) -> dict:
    """Documentos do aluno face aos exigidos: em falta (nunca entregue ou só rejeitado) e por validar."""
    turma = _turma_actual(db, a.estabelecimento_id, a.id)
    docs = list(db.scalars(select(DocumentoAluno).where(DocumentoAluno.aluno_id == a.id).order_by(DocumentoAluno.id)))
    validos = {d.tipo for d in docs if d.estado != "rejeitado"}
    em_falta = [{"tipo": e.tipo, "nome": e.nome, "obrigatorio": e.obrigatorio}
                for e in exigidos(db, a.estabelecimento_id, turma.classe_id if turma else None) if e.tipo not in validos]
    return {"turma": turma, "documentos": docs, "em_falta": em_falta,
            "por_validar": [d for d in docs if d.estado == "recebido"]}


def nome_documento(db: Session, est: int, tipo: str) -> str:
    e = db.scalar(select(DocumentoExigido.nome).where(DocumentoExigido.estabelecimento_id == est,
                                                      DocumentoExigido.tipo == tipo).limit(1))
    return e or tipo.replace("_", " ").capitalize()


def validar_documento(db: Session, d: DocumentoAluno, estado: str, observacao: str | None, por: int) -> None:
    if estado == "rejeitado" and not (observacao or "").strip():
        raise HTTPException(422, "Indique o motivo da rejeição (observacao)")
    d.estado, d.observacao, d.validado_por, d.validado_em = estado, observacao, por, datetime.now(timezone.utc)
    if estado == "rejeitado":
        a = db.get(Aluno, d.aluno_id)
        avisar_encarregados(db, a, "Documento",
                            f"O documento «{nome_documento(db, a.estabelecimento_id, d.tipo)}» de {a.nome_completo} não "
                            f"foi aceite: {observacao} Carregue um novo no portal, em Documentos do educando.")


def encarregados_de(db: Session, aluno_id: int) -> list[tuple[Encarregado, AlunoEncarregado]]:
    return list(db.execute(select(Encarregado, AlunoEncarregado).join(
        AlunoEncarregado, AlunoEncarregado.encarregado_id == Encarregado.id).where(
        AlunoEncarregado.aluno_id == aluno_id).order_by(AlunoEncarregado.principal.desc())).tuples())


def avisar_encarregados(db: Session, a: Aluno, assunto: str, texto: str) -> int:
    """Mensagem (app + WhatsApp) ao encarregado principal do aluno; sem principal, a todos os ligados."""
    ligados = encarregados_de(db, a.id)
    alvo = [ee for ee, ligacao in ligados if ligacao.principal] or [ee for ee, _ in ligados]
    for ee in alvo:
        for canal in ("app", "whatsapp"):
            db.add(Mensagem(estabelecimento_id=a.estabelecimento_id, encarregado_id=ee.id, aluno_id=a.id, canal=canal,
                            assunto=assunto, corpo=texto))
    return len(alvo)


# ---------------------------------------------------------------- declarações
def _campos(texto: str) -> set[str]:
    return {nome for _, nome, _, _ in string.Formatter().parse(texto) if nome}


def validar_modelo(texto: str) -> None:
    """Variáveis conhecidas e marcação aceite pelo gerador de PDF (<b>, <i>, <br/>)."""
    try:
        desconhecidas = _campos(texto) - set(VARIAVEIS)
    except ValueError:
        raise HTTPException(422, "Chavetas desemparelhadas no texto: use {nome}, {classe}…") from None
    if desconhecidas:
        raise HTTPException(422, f"Variáveis desconhecidas: {', '.join(sorted(desconhecidas))}. "
                                 f"Disponíveis: {', '.join('{' + v + '}' for v in VARIAVEIS)}")
    try:
        Paragraph(texto.format(**{v: "x" for v in VARIAVEIS}), pdf.CORPO)
    except Exception as erro:
        raise HTTPException(422, f"Marcação inválida no texto (use só <b>, <i> e <br/>): {erro}") from None


def modelo(db: Session, est: int, codigo: str) -> ModeloDeclaracao:
    m = db.scalar(select(ModeloDeclaracao).where(ModeloDeclaracao.estabelecimento_id == est,
                                                 ModeloDeclaracao.codigo == codigo, ModeloDeclaracao.activo))
    if m is None:
        raise HTTPException(404, f"Modelo de declaração «{codigo}» não existe ou está inactivo")
    return m


def matricula_para_declaracao(db: Session, a: Aluno) -> Matricula:
    """A matrícula activa do ano activo; sem ela, a mais recente (ex.: declaração de um aluno que já saiu)."""
    m = db.scalar(select(Matricula).join(AnoLectivo, AnoLectivo.id == Matricula.ano_id).where(
        Matricula.aluno_id == a.id).order_by((Matricula.estado == "activa").desc(), AnoLectivo.data_inicio.desc()).limit(1))
    if m is None:
        raise HTTPException(409, "O aluno não tem nenhuma matrícula: não é possível passar a declaração")
    return m


def declaracao(db: Session, a: Aluno, md: ModeloDeclaracao, finalidade: str | None = None) -> bytes:
    est = db.get(Estabelecimento, a.estabelecimento_id)
    m = matricula_para_declaracao(db, a)
    t, ano = db.get(Turma, m.turma_id), db.get(AnoLectivo, m.ano_id)
    ee = next((e for e, _ in encarregados_de(db, a.id)), None)
    valores = {"nome": a.nome_completo, "processo": a.numero_processo, "data_nascimento": pdf.dt(a.data_nascimento),
               "bi": a.bi_numero, "classe": db.get(Classe, t.classe_id).designacao, "turma": t.designacao,
               "ano": ano.designacao, "encarregado": ee.nome_completo if ee else None, "escola": est.nome,
               "data": pdf.dt(date.today())}
    texto = md.texto.format(**{k: pdf.esc(v) for k, v in valores.items()})
    if m.estado != "activa":
        texto += f" A matrícula encontra-se no estado: {pdf.esc(m.estado)}."
    blocos = [Spacer(1, 10 * mm), pdf.p(texto, pdf.CORPO), Spacer(1, 6 * mm)]
    if finalidade:
        blocos += [pdf.p(f"A presente declaração destina-se a: {pdf.esc(finalidade)}.", pdf.CORPO), Spacer(1, 4 * mm)]
    blocos += [pdf.p("Por ser verdade e a pedido do(a) interessado(a), passa-se a presente declaração.", pdf.CORPO),
               Spacer(1, 10 * mm), pdf.p(f"Luanda, {pdf.dt(date.today())}"), Spacer(1, 20 * mm),
               pdf.p("_______________________________<br/>A Direcção")]
    return pdf.gerar(est, md.nome, blocos)


# ---------------------------------------------------------------- ficha confirmada pela família na renovação
CAMPOS_ALUNO = ("morada", "bi_numero", "alergias", "necessidades_especiais", "grupo_sanguineo", "condicoes_saude",
                "medicacao", "contacto_emergencia", "telefone_emergencia")
CAMPOS_CONTACTO = ("nome_completo", "telefone", "whatsapp", "email", "profissao", "bi_numero", "nif")
_TELEFONE = re.compile(r"^\+?[\d\s-]{9,16}$")


def ficha(db: Session, a: Aluno) -> dict:
    """Dados do aluno e de todos os contactos ligados (pai, mãe, encarregado…), com avisos de contactos em falta."""
    contactos = [{"encarregado_id": ee.id, "parentesco": lig.parentesco, "principal": lig.principal,
                  "responsavel_financeiro": lig.responsavel_financeiro, **{c: getattr(ee, c) for c in CAMPOS_CONTACTO}}
                 for ee, lig in encarregados_de(db, a.id)]
    avisos = []
    for c in contactos:
        if not c["telefone"] and not c["whatsapp"]:
            avisos.append(f"{c['nome_completo']}: sem telefone")
        elif c["telefone"] and not _TELEFONE.match(c["telefone"]):
            avisos.append(f"{c['nome_completo']}: telefone inválido ({c['telefone']})")
        if not c["email"]:
            avisos.append(f"{c['nome_completo']}: sem e-mail")
    telefones = [c["telefone"] for c in contactos if c["telefone"]]
    avisos += [f"Telefone repetido em vários contactos: {t}" for t in sorted({t for t in telefones if telefones.count(t) > 1})]
    if not {"mae", "pai"} & {c["parentesco"] for c in contactos}:
        avisos.append("Sem contacto do pai nem da mãe")
    return {"aluno_id": a.id, "nome_completo": a.nome_completo, **{c: getattr(a, c) for c in CAMPOS_ALUNO},
            "contactos": contactos, "avisos": avisos}


def aplicar_ficha(db: Session, a: Aluno, ficha_nova: dict) -> list[str]:
    """Actualiza o aluno e os contactos confirmados pela família. Nunca apaga contactos; acrescenta os novos.
    Devolve a lista do que mudou (para o histórico do pedido)."""
    mudou = []
    for campo in CAMPOS_ALUNO:
        if campo in ficha_nova and ficha_nova[campo] != getattr(a, campo):
            setattr(a, campo, ficha_nova[campo])
            mudou.append(f"aluno.{campo}")
    ligados = {ee.id: ee for ee, _ in encarregados_de(db, a.id)}
    for c in ficha_nova.get("contactos") or []:
        dados = {k: c.get(k) for k in CAMPOS_CONTACTO if k in c}
        if c.get("encarregado_id"):
            ee = ligados.get(c["encarregado_id"])
            if ee is None:
                raise HTTPException(422, f"O contacto {c['encarregado_id']} não está ligado a {a.nome_completo}")
            for k, v in dados.items():
                if v != getattr(ee, k):
                    setattr(ee, k, v)
                    mudou.append(f"{ee.nome_completo}.{k}")
            continue
        if not dados.get("nome_completo"):
            continue
        ee = Encarregado(estabelecimento_id=a.estabelecimento_id, **dados)
        db.add(ee)
        db.flush()
        db.add(AlunoEncarregado(aluno_id=a.id, encarregado_id=ee.id, parentesco=c.get("parentesco") or "outro",
                                principal=False, responsavel_financeiro=False))
        mudou.append(f"novo contacto: {ee.nome_completo}")
    return mudou
