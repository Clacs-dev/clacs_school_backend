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
                  AGENDADOR_ACTIVO="false", AUTO_MIGRAR="false", PAGAMENTOS_WEBHOOK_SEGREDO="segredo-exemplos")

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
FILTROS = {"/api/avaliacoes": {"turma_id": 1}, "/api/pautas": {"turma_id": 1, "periodo_id": 1},
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
          ("POST", "/api/transporte/inscricoes", S.InscricaoTransporteIn)]

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


# Rotas que precisam de preparação ou de outros dados além do pedido de exemplo e do id 1.
ESPECIAIS = {
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
