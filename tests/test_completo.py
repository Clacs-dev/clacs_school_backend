"""Funcionalidades da segunda fase: segurança, PDFs, ficheiros, portal, relatórios, importações, fecho do ano."""
import json
import re
from datetime import date
from decimal import Decimal

import pyotp
import pytest

from app import limite_login
from app.config import get_settings
from app.database import SessionLocal
from app.integracoes import email as caixa
from app.integracoes.pagamentos import assinatura
from app.integracoes.primavera import DocumentoExterno, sincronizar
from tests.conftest import login

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF = b"%PDF-1.4\n%teste\n"


@pytest.fixture(scope="module")
def prof(client):
    return login(client, "professor@clacs.ao", get_settings().admin_password)


@pytest.fixture(scope="module")
def ee(client):
    return login(client, "encarregado@clacs.ao", get_settings().admin_password)


# ---------------------------------------------------------------- segurança
def test_bloqueio_depois_de_varias_falhas(client):
    for _ in range(5):
        assert client.post("/api/auth/login", data={"username": "ninguem@x.ao", "password": "errada"}).status_code == 401
    r = client.post("/api/auth/login", data={"username": "ninguem@x.ao", "password": "errada"})
    assert r.status_code == 429 and "Retry-After" in r.headers
    # outro utilizador no mesmo IP continua a poder entrar
    assert client.post("/api/auth/login", data={"username": "professor@clacs.ao",
                                                "password": get_settings().admin_password}).status_code == 200
    limite_login.limpar()


def test_dois_passos(client, admin):
    client.post("/api/utilizadores", headers=admin, json={"nome": "Dois Passos", "email": "2fa@teste.ao",
                                                          "senha": "senha-forte-1", "perfis": ["financeiro"]})
    h = login(client, "2fa@teste.ao", "senha-forte-1")
    segredo = client.post("/api/auth/2fa/iniciar", headers=h).json()["segredo"]
    assert client.post("/api/auth/2fa/activar", headers=h, json={"codigo": "000000"}).status_code == 400
    assert client.post("/api/auth/2fa/activar", headers=h, json={"codigo": pyotp.TOTP(segredo).now()}).status_code == 204
    r = client.post("/api/auth/login", data={"username": "2fa@teste.ao", "password": "senha-forte-1"})
    assert r.status_code == 401 and r.headers.get("X-2FA") == "required"
    r = client.post("/api/auth/login", data={"username": "2fa@teste.ao", "password": "senha-forte-1",
                                             "codigo": pyotp.TOTP(segredo).now()})
    assert r.status_code == 200
    assert "doisfa_segredo" not in client.get("/api/auth/me", headers=h).json()


def test_recuperar_senha(client, admin):
    client.post("/api/utilizadores", headers=admin, json={"nome": "Esqueceu", "email": "esq@teste.ao",
                                                          "senha": "senha-antiga-1", "perfis": ["secretaria"]})
    caixa.CAIXA_LOCAL.clear()
    assert client.post("/api/auth/recuperar", json={"email": "nao-existe@teste.ao"}).status_code == 204
    assert caixa.CAIXA_LOCAL == []
    assert client.post("/api/auth/recuperar", json={"email": "esq@teste.ao"}).status_code == 204
    token = re.search(r"token=([\w.-]+)", caixa.CAIXA_LOCAL[-1].get_content()).group(1)
    assert client.post("/api/auth/redefinir", json={"token": token, "senha_nova": "senha-nova-22"}).status_code == 204
    login(client, "esq@teste.ao", "senha-nova-22")
    # o mesmo link não serve duas vezes
    assert client.post("/api/auth/redefinir", json={"token": token, "senha_nova": "outra-senha-3"}).status_code == 400


def test_professor_so_ve_as_suas_turmas(client, admin, prof):
    turmas = client.get("/api/turmas", headers=prof).json()
    assert [t["designacao"] for t in turmas] == ["5.ª A"]
    outra = next(t for t in client.get("/api/turmas", headers=admin).json() if t["designacao"] == "6.ª B")
    assert client.get("/api/avaliacoes", headers=prof, params={"turma_id": outra["id"]}).status_code == 403
    assert client.get("/api/avaliacoes", headers=prof, params={"turma_id": turmas[0]["id"]}).status_code == 200
    assert client.get("/api/primavera", headers=prof).status_code == 403


def test_cabecalhos_de_seguranca(client):
    r = client.get("/api/health")
    assert r.headers["X-Content-Type-Options"] == "nosniff" and r.headers["X-Request-ID"]


# ---------------------------------------------------------------- PDFs e ficheiros
def test_pdfs(client, admin):
    pg = client.get("/api/pagamentos", headers=admin).json()[0]
    turma = client.get("/api/turmas", headers=admin).json()[0]
    ano = next(a for a in client.get("/api/anos-lectivos", headers=admin).json() if a["estado"] == "activo")
    periodo = client.get(f"/api/anos-lectivos/{ano['id']}/periodos", headers=admin).json()[0]
    mat = client.get("/api/matriculas", headers=admin, params={"turma_id": turma["id"]}).json()[0]
    for url, params in [(f"/api/pagamentos/{pg['id']}/recibo.pdf", {}),
                        (f"/api/alunos/{mat['aluno_id']}/extracto.pdf", {}),
                        (f"/api/alunos/{mat['aluno_id']}/boletim.pdf", {"periodo_id": periodo["id"]}),
                        (f"/api/turmas/{turma['id']}/pauta.pdf", {"periodo_id": periodo["id"]}),
                        (f"/api/matriculas/{mat['id']}/declaracao.pdf", {})]:
        r = client.get(url, headers=admin, params=params)
        assert r.status_code == 200 and r.content.startswith(b"%PDF"), url


def test_envio_e_descarga_de_ficheiros(client, admin):
    r = client.post("/api/alunos/1/foto", headers=admin, files={"ficheiro": ("f.png", PNG, "image/png")})
    assert r.status_code == 200
    url = r.json()["foto_url"]
    assert client.get(url, headers=admin).content == PNG
    r = client.post("/api/alunos/1/documentos/ficheiro", headers=admin, data={"tipo": "BI"},
                    files={"ficheiro": ("bi.pdf", b"isto nao e um pdf", "application/pdf")})
    assert r.status_code == 415
    r = client.post("/api/alunos/1/documentos/ficheiro", headers=admin, data={"tipo": "BI"},
                    files={"ficheiro": ("bi.pdf", PDF, "application/pdf")})
    assert r.status_code == 201
    assert client.get("/api/ficheiros/99/fotos/x.png", headers=admin).status_code == 404
    assert client.get("/api/ficheiros/1/../../etc/passwd", headers=admin).status_code == 404


# ---------------------------------------------------------------- portal do encarregado
def _ana(client, ee) -> int:
    """A conta de demonstração é do encarregado da Ana Cota (pode ter outros educandos importados)."""
    return next(e["id"] for e in client.get("/api/portal/eu", headers=ee).json()["educandos"] if e["nome"] == "Ana Cota")


def test_portal(client, admin, ee):
    aid = _ana(client, ee)
    assert client.get(f"/api/portal/educandos/{aid}", headers=ee).status_code == 200
    outro = client.get("/api/alunos", headers=admin, params={"q": "Diogo"}).json()[0]["id"]
    assert client.get(f"/api/portal/educandos/{outro}", headers=ee).status_code == 404
    assert client.get(f"/api/portal/educandos/{aid}/notas", headers=ee).json()
    assert client.get("/api/alunos", headers=ee).status_code == 403  # sem acesso à área da escola
    assert client.get("/api/portal/eu", headers=admin).status_code == 403


def test_portal_pagamento_por_referencia_e_webhook(client, admin, ee):
    aid = _ana(client, ee)
    # garante uma dívida para este educando
    serv = next(s for s in client.get("/api/servicos", headers=admin).json() if s["codigo"] == "FARD")
    client.post("/api/cobrancas", headers=admin, json={"aluno_id": aid, "servico_id": serv["id"],
                                                       "competencia": date.today().replace(day=1).isoformat()})
    divida = client.get("/api/portal/cobrancas", headers=ee, params={"estado": "divida"}).json()["linhas"]
    alvo = [divida[0]["cobranca_id"]]
    ref = client.post("/api/portal/pagamentos/referencia", headers=ee, json={"cobranca_ids": alvo}).json()
    assert len(ref["referencia"]) == 9
    corpo = json.dumps({"referencia": ref["referencia"], "valor": ref["valor"], "id_transaccao": "T1"}).encode()
    assert client.post("/api/pagamentos/webhook", content=corpo, headers={"X-Assinatura": "falsa"}).status_code == 401
    r = client.post("/api/pagamentos/webhook", content=corpo, headers={"X-Assinatura": assinatura(corpo)})
    assert r.status_code == 200 and r.json()["estado"] == "confirmado"
    pagas = client.get("/api/portal/cobrancas", headers=ee).json()["linhas"]
    assert next(c for c in pagas if c["cobranca_id"] == alvo[0])["estado"] == "paga"
    assert client.get(f"/api/portal/pagamentos/{r.json()['pagamento_id']}/recibo.pdf", headers=ee).content.startswith(b"%PDF")


def test_portal_comprovativo_confirmado_pela_escola(client, admin, ee):
    divida = client.get("/api/portal/cobrancas", headers=ee, params={"estado": "divida"}).json()["linhas"]
    alvo = divida[0]["cobranca_id"]
    r = client.post("/api/portal/pagamentos/comprovativo", headers=ee, data={"cobranca_ids": str(alvo)},
                    files={"ficheiro": ("talao.pdf", PDF, "application/pdf")})
    assert r.status_code == 201
    pid = r.json()["pagamento_id"]
    ainda = client.get("/api/portal/cobrancas", headers=ee).json()["linhas"]
    assert next(c for c in ainda if c["cobranca_id"] == alvo)["estado"] != "paga"
    assert client.post(f"/api/pagamentos/{pid}/confirmar", headers=admin).status_code == 200
    depois = client.get("/api/portal/cobrancas", headers=ee).json()["linhas"]
    assert next(c for c in depois if c["cobranca_id"] == alvo)["estado"] == "paga"


def test_criar_conta_do_portal(client, admin):
    ee_id = client.get("/api/encarregados", headers=admin).json()[3]["id"]
    r = client.post(f"/api/encarregados/{ee_id}/conta", headers=admin, json={"email": "ee4@teste.ao", "senha": "portal-ee-4"})
    assert r.status_code == 201
    h = login(client, "ee4@teste.ao", "portal-ee-4")
    assert client.get("/api/portal/eu", headers=h).status_code == 200
    assert client.post(f"/api/encarregados/{ee_id}/conta", headers=admin,
                       json={"email": "outro@teste.ao", "senha": "portal-ee-4"}).status_code == 409


# ---------------------------------------------------------------- mensagens, relatórios, integrações
def test_email_pela_fila(client, admin):
    caixa.CAIXA_LOCAL.clear()
    client.post("/api/alunos/1/mensagem", headers=admin, json={"corpo": "Reunião sexta", "canais": ["email"]})
    assert client.post("/api/mensagens/despachar", headers=admin).json()["enviadas"] >= 1
    assert any("Reunião sexta" in m.get_content() for m in caixa.CAIXA_LOCAL)


def test_relatorio_agendado(client, admin):
    base = {"nome": "Devedores semanal", "tipo": "devedores", "formato": "xlsx", "destinatarios": ["direccao@clacs.ao"]}
    assert client.post("/api/relatorios/agendados", headers=admin, json={**base, "agenda_cron": "isto nao"}).status_code == 422
    r = client.post("/api/relatorios/agendados", headers=admin, json={**base, "agenda_cron": "0 7 * * 1"}).json()
    assert r["proxima_execucao"]
    caixa.CAIXA_LOCAL.clear()
    ex = client.post(f"/api/relatorios/agendados/{r['id']}/executar", headers=admin).json()
    assert ex["estado"] == "ok" and client.get(ex["ficheiro_url"], headers=admin).content[:2] == b"PK"
    anexos = list(caixa.CAIXA_LOCAL[-1].iter_attachments())
    assert anexos and anexos[0].get_filename().startswith("devedores")
    pdf = client.post("/api/relatorios/agendados", headers=admin,
                      json={**base, "tipo": "cobranca_turma", "formato": "pdf", "agenda_cron": "0 7 1 * *"}).json()
    assert client.post(f"/api/relatorios/agendados/{pdf['id']}/executar", headers=admin).json()["estado"] == "ok"


def test_primavera_com_cliente_falso():
    class Falso:
        def actualizar_cliente(self, e, codigo):
            return codigo

        def documentos_desde(self, desde):
            with SessionLocal() as db:
                from sqlalchemy import select
                from app.models import Cobranca
                c = db.scalar(select(Cobranca).where(Cobranca.estado == "pendente").limit(1))
            return [DocumentoExterno("PRV-1", "fatura_recibo", "FR", "77", date.today(), "000", Decimal("45000"), "comunicado", c.id)]

    with SessionLocal() as db:
        reg = sincronizar(db, 1, cliente=Falso())
        assert reg.estado == "ok" and reg.total_registos > 1
        reg2 = sincronizar(db, 1, cliente=Falso())
        assert reg2.total_registos == 0  # nada mudou: clientes e documento já sincronizados


def test_agendador_liga_e_desliga():
    from app import agendador
    agendador.iniciar()
    try:
        estado = agendador.estado()
        assert estado["activo"] and {t["id"] for t in estado["tarefas"]} == {"tarefas_diarias", "fila", "relatorios"}
    finally:
        agendador.parar()
    assert agendador.estado()["activo"] is False


def test_importar_cobrancas_e_pagamentos(client, admin):
    proc = client.get("/api/alunos", headers=admin, params={"q": "Carla"}).json()[0]["numero_processo"]
    ano = next(a for a in client.get("/api/anos-lectivos", headers=admin).json() if a["estado"] == "activo")
    comp = ano["data_inicio"][:7]  # primeiro mês do ano lectivo
    csv_c = (f"numero_processo;servico_codigo;competencia;valor;vencimento;estado\n"
             f"{proc};MAT;{comp};25.000,00;;paga\n{proc};XYZ;{comp};100;;\n").encode()
    lote = client.post("/api/importacao/cobranca", headers=admin, files={"ficheiro": ("c.csv", csv_c, "text/csv")}).json()
    assert lote["linhas_ok"] == 1 and lote["erros"] == 1
    csv_p = (f"numero_processo;servico_codigo;competencia;valor;data;metodo;referencia\n"
             f"{proc};MAT;{comp};5000;2026-09-15;Transferência BAI;TR1\n").encode()
    lote = client.post("/api/importacao/pagamento", headers=admin, files={"ficheiro": ("p.csv", csv_p, "text/csv")}).json()
    assert lote["erros"] == 1  # a cobrança importada já estava paga


# ---------------------------------------------------------------- fecho do ano (por último: encerra o ano activo)
def test_fecho_do_ano_e_passagem(client, admin):
    ano = next(a for a in client.get("/api/anos-lectivos", headers=admin).json() if a["estado"] == "activo")
    sim = client.post(f"/api/anos-lectivos/{ano['id']}/fechar", headers=admin).json()
    assert sim["simulado"] and sim["totais"]["aprovado"] > 0
    if sim["totais"]["sem_avaliacao"]:
        assert client.post(f"/api/anos-lectivos/{ano['id']}/fechar", headers=admin, params={"simular": False}).status_code == 409
        for l in sim["linhas"]:
            if l["situacao"] is None:
                client.post(f"/api/matriculas/{l['matricula_id']}/anular", headers=admin)
    r = client.post(f"/api/anos-lectivos/{ano['id']}/fechar", headers=admin, params={"simular": False})
    assert r.status_code == 200 and not r.json()["simulado"]
    assert client.get(f"/api/anos-lectivos/{ano['id']}/resultados", headers=admin).json()

    novo = client.post("/api/anos-lectivos", headers=admin,
                       json={"designacao": "2099/2100", "data_inicio": "2099-09-01", "data_fim": "2100-07-15"}).json()
    client.post(f"/api/anos-lectivos/{novo['id']}/activar", headers=admin)
    classe = client.get("/api/classes", headers=admin).json()[0]
    t_novo = client.post("/api/turmas", headers=admin, json={"designacao": "6.ª A", "classe_id": classe["id"],
                                                             "capacidade": 40}).json()
    antigas = {l["turma"] for l in sim["linhas"]}
    turmas_antigas = [t for t in client.get("/api/turmas", headers=admin, params={"ano_id": ano["id"]}).json()
                      if t["designacao"] in antigas]
    pares = [{"de": t["id"], "para": t_novo["id"]} for t in turmas_antigas]
    res = client.post(f"/api/anos-lectivos/{novo['id']}/transitar", headers=admin,
                      json={"de_ano_id": ano["id"], "turmas": pares, "repetentes": pares}).json()
    assert res["criadas"] > 0
