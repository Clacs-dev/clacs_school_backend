"""Gera app/exemplos_respostas.json: uma resposta real de cada rota JSON, para o "Example Value" do /api/docs.

    python -m scripts.gerar_exemplos

Usa uma base SQLite temporária com os dados de demonstração (não toca na base configurada no .env). Cada rota
corre sobre uma cópia limpa dessa base, com o pedido de exemplo de app/schemas.py, para que nenhuma chamada
estrague a seguinte. Volte a correr depois de mudar as respostas; o teste test_exemplos_de_resposta avisa se
ficarem desactualizados ou se faltar alguma rota.
"""
import json
import os
import re
import shutil
import tempfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DESTINO = RAIZ / "app" / "exemplos_respostas.json"
SENHA = "senha-de-exemplo-123"

_tmp = Path(tempfile.mkdtemp())
BASE, MODELO = _tmp / "exemplos.db", _tmp / "modelo.db"
os.environ.update(DATABASE_URL=f"sqlite:///{BASE.as_posix()}", ADMIN_PASSWORD=SENHA,
                  JWT_SECRET="segredo-so-para-gerar-exemplos-123456", UPLOAD_DIR=str(_tmp / "uploads"),
                  AGENDADOR_ACTIVO="false", AUTO_MIGRAR="false", PAGAMENTOS_WEBHOOK_SEGREDO="segredo-exemplos",
                  GOOGLE_FORMS_SEGREDO="segredo-formulario-exemplos",
                  SMTP_HOST="", EVOLUTION_URL="", ZAPI_INSTANCE_ID="", WHATSAPP_TOKEN="", TWILIO_ACCOUNT_SID="")

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.routing import APIRoute  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app import schemas as S  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.database import SessionLocal, engine  # noqa: E402
from app.integracoes.pagamentos import assinatura  # noqa: E402
from app.main import app  # noqa: E402
from app.models import AnoLectivo, DocumentoFiscal, Matricula, ResultadoFinal  # noqa: E402
from scripts.seed import base, demo  # noqa: E402

MAX_ITENS = 2  # listas de registos encurtadas para o exemplo ficar legível
FILTROS = {"/api/aulas": {"data": "2026-11-02"}, "/api/assiduidade/relatorio": {"turma_id": 1, "desde": "2026-11-01", "ate": "2026-11-30"},
           "/api/professores/assiduidade": {"desde": "2026-11-02", "ate": "2026-11-02"},
           "/api/avaliacoes": {"turma_id": 1}, "/api/pautas": {"turma_id": 1, "periodo_id": 1},
           "/api/registo-diario": {"turma_id": 1, "data": "2026-10-20"},
           "/api/refeitorio/registos": {"data": "2026-10-20"},
           "/api/turmas/sugestao": {"turma_id": 1}, "/api/pautas/final": {"turma_id": 1},
           "/api/portal/educandos/{aid}/calendario": {"desde": "2026-01-01"}}  # parâmetros obrigatórios ou datas dos exemplos

# Registos que os dados de demonstração não trazem, criados com os pedidos de exemplo para que as listas
# correspondentes também tenham uma resposta de exemplo.
POVOAR = [("POST", "/api/comunicados", S.ComunicadoIn), ("POST", "/api/alunos/1/mensagem", S.MensagemAlunoIn),
          ("POST", "/api/ocorrencias", S.OcorrenciaIn), ("POST", "/api/registo-diario", S.RegistoDiarioIn),
          ("PUT", "/api/refeitorio/ementas", S.EmentaIn), ("POST", "/api/refeitorio/registos", S.RefeicaoRegistoIn),
          ("POST", "/api/matriculas/1/turma", S.MudancaTurmaIn),
          ("POST", "/api/matriculas/1/descontos", S.MatriculaDescontoIn),
          ("POST", "/api/relatorios/agendados", S.RelatorioIn), ("POST", "/api/relatorios/agendados/1/executar", None),
          ("POST", "/api/alertas/gerar", None), ("POST", "/api/portal/pedidos", S.PedidoIn),
          ("POST", "/api/avaliacoes", S.AvaliacaoIn), ("PUT", "/api/turmas/2/atribuicoes", S.AtribuicaoIn),
          ("POST", "/api/portal/avaliacoes-professores", S.AvaliacaoProfessorIn),
          ("POST", "/api/transporte/viaturas", S.ViaturaIn), ("POST", "/api/transporte/rotas", S.RotaIn),
          ("POST", "/api/transporte/inscricoes", S.InscricaoTransporteIn), ("POST", "/api/candidaturas", S.CandidaturaIn),
          ("POST", "/api/saude/ocorrencias", S.OcorrenciaSaudeIn), ("POST", "/api/saude/medicacao", S.MedicacaoIn),
          ("POST", "/api/acordos-modelos", S.AcordoModeloIn), ("POST", "/api/acordos", S.AcordoIn),
          ("POST", "/api/orcamentos", S.OrcamentoIn), ("POST", "/api/orcamentos/1/linhas", S.OrcamentoLinhaIn),
          ("POST", "/api/despesas", S.DespesaIn), ("POST", "/api/eventos", S.EventoIn), ("POST", "/api/horarios", S.HorarioIn),
          ("POST", "/api/substituicoes", S.SubstituicaoIn), ("POST", "/api/aulas/registar", S.AulaRegistoIn),
          ("POST", "/api/modelos-declaracao", S.ModeloDeclaracaoIn), ("POST", "/api/documentos-exigidos", S.DocumentoExigidoIn)]

PDF = ("comprovativo.pdf", b"%PDF-1.4\n%exemplo\n", "application/pdf")
CSV_ALUNOS = ("nome_completo;numero_processo;data_nascimento;sexo;bi_numero;turma;encarregado_nif;encarregado_nome;"
              "parentesco\nBeatriz Lunda;2026-0101;2016-05-02;F;;5.ª A;;Rosa Lunda;mãe\n"
              "Carlos Neto;2026-0102;2015-11-20;M;;9.ª Z;;Paulo Neto;pai\n").encode()


def _ano_fechado():
    """Fecho do ano 2026/2027 directamente na base: os dados de demonstração não têm notas de todos os alunos."""
    with SessionLocal() as db:
        for i, m in enumerate(db.scalars(select(Matricula).where(Matricula.ano_id == 2, Matricula.estado == "activa"))):
            db.add(ResultadoFinal(matricula_id=m.id, media_final=Decimal("13.5") if i % 4 else Decimal("8.0"),
                                  situacao="aprovado" if i % 4 else "reprovado", fechada_em=datetime(2027, 7, 20)))
            m.estado = "concluida"
        db.get(AnoLectivo, 2).estado = "encerrado"
        db.commit()


def _documentos_primavera():
    with SessionLocal() as db:
        db.add_all([DocumentoFiscal(estabelecimento_id=1, tipo="fatura_recibo", serie="FR 2026", numero=str(n),
                                    data_emissao=date(2026, 10, 8), nif_cliente="004567891LA033",
                                    total=Decimal(18000), id_primavera=f"PRI-{n}", estado_agt="comunicado")
                    for n in (1045, 1046)])
        db.commit()


def _transitar(pedir):
    _ano_fechado()
    novo = pedir("POST", "/api/anos-lectivos", json=S.EXEMPLOS[S.AnoIn]).json()["id"]
    turma = pedir("POST", "/api/turmas", json={"designacao": "6.ª A", "classe_id": 2, "ano_id": novo}).json()["id"]
    return pedir("POST", f"/api/anos-lectivos/{novo}/transitar",
                 json={"de_ano_id": 2, "turmas": [{"de": 1, "para": turma}], "copiar_servicos": True})


def _webhook(pedir):
    ref = pedir("POST", "/api/portal/pagamentos/referencia", portal=True, json={"cobranca_ids": [36]}).json()
    corpo = json.dumps({"referencia": ref["referencia"], "valor": ref["valor"], "data": "2026-10-08",
                        "id_transaccao": "GW-778812"}).encode()
    return pedir("POST", "/api/pagamentos/webhook", content=corpo,
                 headers={"X-Assinatura": assinatura(corpo), "Content-Type": "application/json"})


def _importar(pedir):
    return pedir("POST", "/api/importacao/aluno", data={"origem": "Software anterior (2025)"},
                 files={"ficheiro": ("alunos.csv", CSV_ALUNOS, "text/csv")})


PASSOS_CANDIDATURA = [("reuniao", S.ReuniaoIn), ("avaliacao", S.AvaliacaoCandidaturaIn), ("validar", S.ParecerIn),
                      ("autorizar", S.ParecerIn), ("comunicar", S.ComunicarVagaIn)]


def _candidatura(pedir, ate: str | None = None, entregar=False):
    """Leva a candidatura 1 (criada em POVOAR) até ao passo indicado; devolve o token do link da família."""
    token = None
    for passo, esquema in PASSOS_CANDIDATURA:
        if passo == ate:
            break
        r = pedir("POST", f"/api/candidaturas/1/{passo}", json=S.EXEMPLOS[esquema]).json()
        token = (r.get("link_formalizacao") or "").rsplit("/", 1)[-1] or None
    if entregar:
        _preencher(pedir, token)
        pedir("POST", f"/api/publico/candidaturas/{token}/submeter", anonimo=True)
    return token


def _preencher(pedir, token):
    pub = f"/api/publico/candidaturas/{token}"
    pedir("PUT", pub, anonimo=True, json=S.EXEMPLOS[S.FormalizacaoIn])
    for tipo in ("cedula_bi_aluno", "fotografia", "bi_encarregado"):
        pedir("POST", f"{pub}/documentos", anonimo=True, data={"tipo": tipo}, files={"ficheiro": PDF})


def _submeter(pedir):
    token = _candidatura(pedir)
    _preencher(pedir, token)
    return pedir("POST", f"/api/publico/candidaturas/{token}/submeter", anonimo=True)


def _apagar_documento(pedir):
    pub = f"/api/publico/candidaturas/{_candidatura(pedir)}"
    _preencher(pedir, pub.rsplit("/", 1)[1])
    doc = pedir("POST", f"{pub}/documentos", anonimo=True, data={"tipo": "outro"}, files={"ficheiro": PDF}).json()
    return pedir("DELETE", f"{pub}/documentos/{doc['documentos'][-1]['id']}", anonimo=True)


def _publico(metodo, sufixo="", **kw):
    return lambda pedir: pedir(metodo, f"/api/publico/candidaturas/{_candidatura(pedir)}{sufixo}", anonimo=True, **kw)


CSV_FORMS = ("Carimbo de data/hora,Nome completo do aluno,Data de nascimento,Classe pretendida,"
             "Nome do encarregado de educação,Telefone / WhatsApp\n"
             "2026/11/01 09:00,Beatriz Lunda,02/05/2019,1.ª Classe,Rosa Lunda,+244922000111\n").encode()


# Rotas que precisam de preparação ou de outros dados além do pedido de exemplo e do id 1.
def _pedido(pedir, **corpo):
    return pedir("POST", "/api/portal/pedidos", portal=True, json=corpo).json()["id"]


def _prestacoes(pedir):
    total = pedir("GET", "/api/acordos/1").json()["valor_total"]
    metade = round(total / 2, 2)
    return pedir("PUT", "/api/acordos/1/prestacoes", json={"motivo": "Pedido do encarregado: adiar a 2.ª prestação", "prestacoes": [
        {"valor": metade, "vencimento": "2026-12-10"}, {"valor": round(total - metade, 2), "vencimento": "2027-02-10"}]})


def _documento_portal(pedir):
    return pedir("POST", "/api/portal/educandos/1/documentos", portal=True, data={"tipo": "boletim_vacinas"},
                 files={"ficheiro": ("boletim.pdf", PDF[1], PDF[2])})


ESPECIAIS = {
    "POST /api/horarios": lambda pedir: pedir("POST", "/api/horarios", json={
        **S.EXEMPLOS[S.HorarioIn], "tempo": 2, "hora_inicio": "08:20:00", "hora_fim": "09:05:00"}),
    "POST /api/acordos": lambda pedir: (pedir("POST", "/api/acordos/1/cancelar", json=S.EXEMPLOS[S.MotivoIn]),
                                         pedir("POST", "/api/acordos", json=S.EXEMPLOS[S.AcordoIn]))[1],
    "POST /api/orcamentos": lambda pedir: pedir("POST", "/api/orcamentos", json={
        **S.EXEMPLOS[S.OrcamentoIn], "designacao": "Orçamento rectificativo 2026/2027"}),
    "POST /api/documentos-exigidos": lambda pedir: pedir("POST", "/api/documentos-exigidos", json={
        **S.EXEMPLOS[S.DocumentoExigidoIn], "tipo": "atestado_robustez", "nome": "Atestado de robustez física"}),
    "POST /api/modelos-declaracao": lambda pedir: pedir("POST", "/api/modelos-declaracao", json={
        **S.EXEMPLOS[S.ModeloDeclaracaoIn], "codigo": "abono_2"}),
    "GET /api/portal/educandos/{aid}/horario": lambda pedir: (  # o aluno 1 passou para a turma 2 em POVOAR
        pedir("POST", "/api/horarios", json={**S.EXEMPLOS[S.HorarioIn], "turma_id": 2, "funcionario_id": None, "sala_id": None}),
        pedir("GET", "/api/portal/educandos/1/horario", portal=True))[1],
    "GET /api/contactos/avisos": lambda pedir: (pedir("PATCH", "/api/encarregados/1", json={"email": None}),
                                              pedir("GET", "/api/contactos/avisos"))[1],
    "POST /api/pedidos/{pid}/concluir": lambda pedir: pedir(
        "POST", f"/api/pedidos/{_pedido(pedir, tipo='financeiro', assunto='Propina de Outubro', mensagem='Já paguei.')}/concluir",
        json=S.EXEMPLOS[S.ConcluirIn]),
    "POST /api/portal/pedidos/{pid}/responder": lambda pedir: (
        pedir("POST", "/api/pedidos/1/pedir-informacao", json=S.EXEMPLOS[S.MensagemPedidoIn]),
        pedir("POST", "/api/portal/pedidos/1/responder", portal=True, json={"mensagem": "A avó vem buscá-lo às 15h."}))[1],
    "POST /api/portal/pedidos/{pid}/anexos": lambda pedir: pedir(
        "POST", "/api/portal/pedidos/1/anexos", portal=True, files={"ficheiro": ("atestado.pdf", PDF[1], PDF[2])}),
    "POST /api/portal/educandos/{aid}/documentos": _documento_portal,
    "POST /api/alunos/{aid}/documentos/{did}/validar": lambda pedir: pedir(
        "POST", f"/api/alunos/1/documentos/{_documento_portal(pedir).json()['id']}/validar", json=S.EXEMPLOS[S.ValidarDocumentoIn]),
    "GET /api/documentos": lambda pedir: (_documento_portal(pedir), pedir("GET", "/api/documentos"))[1],
    "POST /api/portal/educandos/{aid}/medicacao": lambda pedir: pedir(
        "POST", "/api/portal/educandos/1/medicacao", portal=True, files={"receita": ("receita.pdf", PDF[1], PDF[2])},
        data={"medicamento": "Paracetamol xarope", "dose": "5 ml", "horario": "Se febre acima de 38 graus",
              "data_inicio": "2026-11-04", "data_fim": "2026-11-08", "confirmo": "true"}),
    "POST /api/saude/medicacao/{mid}/administracoes": lambda pedir: (
        pedir("POST", "/api/saude/medicacao/1/decidir", json={"autorizar": True}),
        pedir("POST", "/api/saude/medicacao/1/administracoes", json=S.EXEMPLOS[S.AdministracaoIn]))[1],
    "POST /api/saude/medicacao/{mid}/terminar": lambda pedir: (
        pedir("POST", "/api/saude/medicacao/1/decidir", json={"autorizar": True}),
        pedir("POST", "/api/saude/medicacao/1/terminar"))[1],
    "PUT /api/acordos/{aid}/prestacoes": _prestacoes,
    "GET /api/portal/acordos": lambda pedir: (pedir("POST", "/api/acordos/1/aprovar"),
                                              pedir("GET", "/api/portal/acordos", portal=True))[1],
    "POST /api/auth/login": lambda pedir: pedir("POST", "/api/auth/login", anonimo=True,
                                                data={"username": "admin@clacs.ao", "password": SENHA}),
    "POST /api/anos-lectivos/{ano_id}/periodos": lambda pedir: pedir(
        "POST", f"/api/anos-lectivos/{pedir('POST', '/api/anos-lectivos', json={**S.EXEMPLOS[S.AnoIn], 'criar_trimestres': False}).json()['id']}/periodos",
        json=S.EXEMPLOS[S.PeriodoIn]),
    "POST /api/classes/{classe_id}/disciplinas": lambda pedir: (
        pedir("POST", "/api/disciplinas", json=S.EXEMPLOS[S.DisciplinaIn]),
        pedir("POST", "/api/classes/1/disciplinas", json=S.EXEMPLOS[S.ClasseDisciplinaIn]))[1],
    "GET /api/anos-lectivos/{ano_id}/resultados": lambda pedir: (_ano_fechado(),
                                                                 pedir("GET", "/api/anos-lectivos/2/resultados"))[1],
    "POST /api/anos-lectivos/{ano_id}/transitar": _transitar,
    "POST /api/cobrancas/{cid}/anular": lambda pedir: pedir("POST", "/api/cobrancas/36/anular"),
    "POST /api/pautas/fechar": lambda pedir: pedir("POST", "/api/pautas/fechar", params={"turma_id": 1, "periodo_id": 1}),
    "PATCH /api/relatorios/agendados/{rid}": lambda pedir: pedir("PATCH", "/api/relatorios/agendados/1",
                                                                params={"activo": False}),
    "POST /api/primavera/divergencias/{did}/{accao}": lambda pedir: pedir(
        "POST", "/api/primavera/divergencias/1/resolver"),
    "GET /api/primavera/documentos": lambda pedir: (_documentos_primavera(),
                                                    pedir("GET", "/api/primavera/documentos"))[1],
    "POST /api/importacao/{entidade}": _importar,
    "GET /api/importacao/lotes": lambda pedir: (_importar(pedir), pedir("GET", "/api/importacao/lotes"))[1],
    "GET /api/importacao/lotes/{lid}/erros": lambda pedir: (_importar(pedir),
                                                            pedir("GET", "/api/importacao/lotes/1/erros"))[1],
    "POST /api/encarregados/{eid}/conta": lambda pedir: pedir("POST", "/api/encarregados/2/conta",
                                                              json={"email": "pai.silva@gmail.com",
                                                                    "senha": "Primeira-senha-2026"}),
    "POST /api/pagamentos/webhook": _webhook,
    "POST /api/transporte/viaturas": lambda pedir: pedir(  # a base de exemplo já tem a viatura e a rota do exemplo
        "POST", "/api/transporte/viaturas", json={**S.EXEMPLOS[S.ViaturaIn], "matricula": "LD-77-31-CC"}),
    "POST /api/transporte/rotas": lambda pedir: pedir(
        "POST", "/api/transporte/rotas", json={**S.EXEMPLOS[S.RotaIn], "nome": "Rota Kilamba"}),
    "POST /api/transporte/inscricoes": lambda pedir: pedir(  # o aluno 1 já está inscrito na base de exemplo
        "POST", "/api/transporte/inscricoes", json={**S.EXEMPLOS[S.InscricaoTransporteIn], "aluno_id": 2}),
    "POST /api/alunos/{aid}/documentos/ficheiro": lambda pedir: pedir(
        "POST", "/api/alunos/1/documentos/ficheiro", data={"tipo": "certidao_nascimento"},
        files={"ficheiro": ("certidao.pdf", PDF[1], PDF[2])}),
    "PUT /api/perfis/{pid}/permissoes": lambda pedir: pedir(  # o perfil 1 é o administrador (não se edita)
        "PUT", "/api/perfis/3/permissoes", json=S.EXEMPLOS[S.PermissoesPerfilIn]),
    "POST /api/ocorrencias/{oid}/imagens": lambda pedir: pedir(
        "POST", "/api/ocorrencias/1/imagens", files={"ficheiro": ("vidro-partido.png", b"\x89PNG\r\n\x1a\nexemplo", "image/png")}),
    "POST /api/auth/me/foto": lambda pedir: pedir(
        "POST", "/api/auth/me/foto", files={"ficheiro": ("eu.png", b"\x89PNG\r\n\x1a\nexemplo", "image/png")}),
    "POST /api/alunos/{aid}/foto": lambda pedir: pedir(
        "POST", "/api/alunos/1/foto", files={"ficheiro": ("foto.png", b"\x89PNG\r\n\x1a\nexemplo", "image/png")}),
    "POST /api/pagamentos/{pid}/comprovativo": lambda pedir: pedir(
        "POST", "/api/pagamentos/1/comprovativo", files={"ficheiro": PDF}),
    "POST /api/candidaturas/{cid}/avaliacao": lambda pedir: (_candidatura(pedir, "avaliacao"), pedir(
        "POST", "/api/candidaturas/1/avaliacao", json=S.EXEMPLOS[S.AvaliacaoCandidaturaIn]))[1],
    "POST /api/candidaturas/{cid}/validar": lambda pedir: (_candidatura(pedir, "validar"), pedir(
        "POST", "/api/candidaturas/1/validar", json=S.EXEMPLOS[S.ParecerIn]))[1],
    "POST /api/candidaturas/{cid}/autorizar": lambda pedir: (_candidatura(pedir, "autorizar"), pedir(
        "POST", "/api/candidaturas/1/autorizar", json=S.EXEMPLOS[S.ParecerIn]))[1],
    "POST /api/candidaturas/{cid}/comunicar": lambda pedir: (_candidatura(pedir, "comunicar"), pedir(
        "POST", "/api/candidaturas/1/comunicar", json=S.EXEMPLOS[S.ComunicarVagaIn]))[1],
    "POST /api/candidaturas/{cid}/correccao": lambda pedir: (_candidatura(pedir, entregar=True), pedir(
        "POST", "/api/candidaturas/1/correccao", json=S.EXEMPLOS[S.CorreccaoIn]))[1],
    "POST /api/candidaturas/{cid}/matricular": lambda pedir: (_candidatura(pedir, entregar=True), pedir(
        "POST", "/api/candidaturas/1/matricular", json=S.EXEMPLOS[S.MatricularCandidaturaIn]))[1],
    "POST /api/candidaturas/{cid}/boas-vindas": lambda pedir: (
        _candidatura(pedir, entregar=True), pedir("POST", "/api/candidaturas/1/matricular", json={}),
        pedir("POST", "/api/candidaturas/1/boas-vindas", json=S.EXEMPLOS[S.BoasVindasIn]))[2],
    "POST /api/candidaturas/importar": lambda pedir: pedir(
        "POST", "/api/candidaturas/importar", files={"ficheiro": ("respostas.csv", CSV_FORMS, "text/csv")}),
    "POST /api/publico/candidaturas/google-forms": lambda pedir: pedir(
        "POST", "/api/publico/candidaturas/google-forms", anonimo=True, json=S.EXEMPLOS[S.GoogleFormsIn],
        headers={"X-Clacs-Segredo": "segredo-formulario-exemplos"}),
    "GET /api/publico/candidaturas/{token}": _publico("GET"),
    "PUT /api/publico/candidaturas/{token}": _publico("PUT", json=S.EXEMPLOS[S.FormalizacaoIn]),
    "POST /api/publico/candidaturas/{token}/documentos": _publico(
        "POST", "/documentos", data={"tipo": "cedula_bi_aluno"}, files={"ficheiro": PDF}),
    "DELETE /api/publico/candidaturas/{token}/documentos/{did}": _apagar_documento,
    "POST /api/publico/candidaturas/{token}/submeter": _submeter,
    "POST /api/portal/pagamentos/comprovativo": lambda pedir: pedir(
        "POST", "/api/portal/pagamentos/comprovativo", portal=True, data={"cobranca_ids": "36", "referencia": "TRF-0045821"},
        files={"ficheiro": PDF}),
}

# Respostas com segredos de sessão: o exemplo mostra o formato, não um valor utilizável.
OCULTAR = {"access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.exemplo.assinatura"}


def encurtar(valor):
    if isinstance(valor, list):
        registos = valor and all(isinstance(v, dict) for v in valor)
        return [encurtar(v) for v in (valor[:MAX_ITENS] if registos else valor)]
    if isinstance(valor, dict):
        return {k: OCULTAR.get(k, encurtar(v)) for k, v in valor.items()}
    return valor


def repor():
    """Volta à base modelo (dados de demonstração + POVOAR) antes de cada chamada."""
    engine.dispose()
    shutil.copyfile(MODELO, BASE)


def main():
    cfg = Config(str(RAIZ / "alembic.ini"))
    cfg.set_main_option("script_location", str(RAIZ / "alembic"))
    command.upgrade(cfg, "head")
    with SessionLocal() as db:
        demo(db, base(db), hoje=date(2026, 10, 15))

    cliente = TestClient(app)

    def entrar(email):
        r = cliente.post("/api/auth/login", data={"username": email, "password": SENHA})
        r.raise_for_status()
        return {"Authorization": "Bearer " + r.json()["access_token"]}

    admin = entrar(get_settings().admin_email)
    encarregado = entrar("encarregado@clacs.ao")

    for metodo, caminho, esquema in POVOAR:
        r = cliente.request(metodo, caminho, json=S.EXEMPLOS[esquema] if esquema else None,
                            headers=encarregado if caminho.startswith("/api/portal") else admin)
        if r.status_code >= 400:
            print(f"  aviso: {metodo} {caminho} -> {r.status_code} {r.text[:120]}")
    engine.dispose()
    shutil.copyfile(BASE, MODELO)

    def pedir(metodo, caminho, portal=False, anonimo=False, headers=None, **kw):
        sessao = {} if anonimo else encarregado if portal or caminho.startswith("/api/portal") else admin
        return cliente.request(metodo, caminho, headers={**sessao, **(headers or {})}, **kw)

    corpos = {esquema.__name__: exemplo for esquema, exemplo in S.EXEMPLOS.items()}
    operacoes = app.openapi()["paths"]
    exemplos, falhadas = {}, []
    for rota in app.routes:
        if not isinstance(rota, APIRoute) or not rota.include_in_schema or rota.response_model is None:
            continue  # ficheiros (PDF, Excel) e respostas 204 sem corpo
        for metodo in sorted(rota.methods):
            chave = f"{metodo} {rota.path}"
            repor()
            if chave in ESPECIAIS:
                r = ESPECIAIS[chave](pedir)
            else:
                corpo = operacoes[rota.path][metodo.lower()].get("requestBody", {}).get("content", {})
                pedido = {"params": FILTROS.get(rota.path)}
                if corpo:
                    pedido["json"] = corpos[corpo["application/json"]["schema"]["$ref"].split("/")[-1]]
                r = pedir(metodo, re.sub(r"\{[^}]+\}", "1", rota.path), **pedido)
            if r.status_code in (200, 201) and r.json() != []:  # lista vazia: fica o exemplo gerado do esquema
                exemplos[chave] = encurtar(r.json())
            else:
                falhadas.append(f"{chave} -> {r.status_code} {r.text[:150]}")

    DESTINO.write_text(json.dumps(exemplos, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"{len(exemplos)} exemplos gravados em {DESTINO.relative_to(RAIZ)}")
    for linha in falhadas:
        print("  sem exemplo:", linha)


if __name__ == "__main__":
    main()
