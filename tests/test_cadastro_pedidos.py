"""Bloco E: pedidos do encarregado pelo portal (vaga, matrícula, transferência, declaração, justificação de falta)
e a decisão da escola. Corre antes de test_completo.py, cujo último teste encerra o ano lectivo activo."""
from datetime import date, timedelta

from tests.conftest import login

SENHA = "senha-de-teste-123"


def _ok(r, *codigos):
    assert r.status_code in (codigos or (200, 201, 204)), f"{r.request.method} {r.request.url.path}: {r.status_code} {r.text}"
    return r.json() if r.content and r.headers.get("content-type", "").startswith("application/json") else r


def _ee(client):
    return login(client, "encarregado@clacs.ao", SENHA)


def _aviso(client, admin, pid):
    return [a for a in client.get("/api/alertas", headers=admin, params={"tipo": "pedido_encarregado"}).json()
            if a["dados"]["pedido_id"] == pid]


def test_vaga_aprovada_cria_aluno_matriculado_e_depois_transferencia(client, admin):
    ee = _ee(client)
    turma = next(t for t in client.get("/api/turmas", headers=admin).json() if not t["cheia"])
    p = _ok(client.post("/api/portal/pedidos", headers=ee, json={
        "tipo": "vaga", "nome_aluno": "Pedro Cota", "data_nascimento": "2020-05-02", "sexo": "M",
        "classe_id": turma["classe_id"]}), 201)
    assert p["estado"] == "pendente" and p["aluno"] == "Pedro Cota" and p["aluno_id"] is None
    assert [a["estado"] for a in _aviso(client, admin, p["id"])] == ["novo"]  # a escola é avisada
    assert any(x["id"] == p["id"] for x in client.get("/api/pedidos", headers=admin, params={"estado": "pendente"}).json())

    feito = _ok(client.post(f"/api/pedidos/{p['id']}/aprovar", headers=admin, json={"turma_id": turma["id"]}))
    assert feito["estado"] == "aprovado" and feito["resultado"]["turma_id"] == turma["id"]
    aid = feito["aluno_id"]
    assert [a["estado"] for a in _aviso(client, admin, p["id"])] == ["tratado"]
    assert aid in [e["id"] for e in client.get("/api/portal/eu", headers=ee).json()["educandos"]]  # novo educando
    client.post("/api/mensagens/despachar", headers=admin)
    assert any("vaga foi aprovado" in m["corpo"] for m in client.get("/api/portal/mensagens", headers=ee).json())
    assert client.post(f"/api/pedidos/{p['id']}/aprovar", headers=admin, json={}).status_code == 409  # já decidido

    # o mesmo educando pede transferência: fica transferido e a matrícula é anulada
    t = _ok(client.post("/api/portal/pedidos", headers=ee, json={
        "tipo": "transferencia", "aluno_id": aid, "escola_destino": "Escola Primária do Kilamba"}), 201)
    feito = _ok(client.post(f"/api/pedidos/{t['id']}/aprovar", headers=admin, json={}))
    assert feito["resultado"]["matricula_anulada"] == _ok(client.get(f"/api/pedidos/{p['id']}", headers=admin))["resultado"]["matricula_id"]
    assert _ok(client.get(f"/api/alunos/{aid}", headers=admin))["estado"] == "transferido"


def test_validacoes_dos_pedidos(client, admin):
    ee = _ee(client)
    assert client.post("/api/portal/pedidos", headers=ee, json={"tipo": "vaga", "nome_aluno": "Sem classe"}).status_code == 422
    alheio = next(a["id"] for a in client.get("/api/alunos", headers=admin).json()
                  if a["id"] not in [e["id"] for e in client.get("/api/portal/eu", headers=ee).json()["educandos"]])
    r = client.post("/api/portal/pedidos", headers=ee, json={"tipo": "declaracao", "aluno_id": alheio, "finalidade": "x"})
    assert r.status_code == 404  # só os seus educandos
    meu = client.get("/api/portal/eu", headers=ee).json()["educandos"][0]["id"]
    pedido = {"tipo": "declaracao", "aluno_id": meu, "finalidade": "Abono de família"}
    p = _ok(client.post("/api/portal/pedidos", headers=ee, json=pedido), 201)
    assert client.post("/api/portal/pedidos", headers=ee, json=pedido).status_code == 409  # já há um pendente
    assert client.post(f"/api/pedidos/{p['id']}/rejeitar", headers=admin, json={}).status_code == 422  # sem motivo
    r = _ok(client.post(f"/api/pedidos/{p['id']}/rejeitar", headers=admin, json={"resposta": "Falta o BI do aluno."}))
    assert r["estado"] == "rejeitado" and r["resposta"] == "Falta o BI do aluno."
    c = _ok(client.post("/api/portal/pedidos", headers=ee, json=pedido), 201)
    assert _ok(client.post(f"/api/portal/pedidos/{c['id']}/cancelar", headers=ee))["estado"] == "cancelado"
    assert client.post(f"/api/portal/pedidos/{c['id']}/cancelar", headers=ee).status_code == 409


def test_declaracao_pedida_e_descarregada(client, admin):
    ee = _ee(client)
    meu = client.get("/api/portal/eu", headers=ee).json()["educandos"][0]["id"]
    p = _ok(client.post("/api/portal/pedidos", headers=ee, json={
        "tipo": "declaracao", "aluno_id": meu, "finalidade": "Inscrição no ATL"}), 201)
    assert client.get(f"/api/portal/pedidos/{p['id']}/declaracao.pdf", headers=ee).status_code == 409
    _ok(client.post(f"/api/pedidos/{p['id']}/aprovar", headers=admin, json={}))
    pdf = client.get(f"/api/portal/pedidos/{p['id']}/declaracao.pdf", headers=ee)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")


def test_justificacao_de_falta_e_historico(client, admin):
    ee = _ee(client)
    meu = client.get("/api/portal/eu", headers=ee).json()["educandos"][0]["id"]
    aluno = next(a for a in client.get("/api/alunos", headers=admin).json() if a["id"] == meu)
    dia = (date.today() - timedelta(days=1)).isoformat()
    _ok(client.post("/api/assiduidade", headers=admin, json={
        "turma_id": aluno["turma_id"], "data": dia, "registos": [{"aluno_id": meu, "estado": "falta_injustificada"}]}))
    p = _ok(client.post("/api/portal/pedidos", headers=ee, json={
        "tipo": "justificacao_falta", "aluno_id": meu, "data": dia, "motivo": "Consulta no pediatra"}), 201)

    # a secretaria (sem pedagogico/editar) não decide justificações; a direcção sim
    secretaria = login(client, "secretaria@teste.ao", "secretaria-123")
    assert client.post(f"/api/pedidos/{p['id']}/aprovar", headers=secretaria, json={}).status_code == 403
    feito = _ok(client.post(f"/api/pedidos/{p['id']}/aprovar", headers=admin, json={}))
    assert feito["resultado"] == {"faltas_justificadas": 1}
    registo = next(r for r in client.get("/api/assiduidade", headers=admin, params={"turma_id": aluno["turma_id"], "data": dia}).json()
                   if r["aluno_id"] == meu)
    assert (registo["estado"], registo["justificacao"]) == ("falta_justificada", "Consulta no pediatra")
    historico = _ok(client.get("/api/portal/pedidos", headers=ee, params={"tipo": "justificacao_falta"}))
    assert [h["id"] for h in historico][:1] == [p["id"]] and historico[0]["estado"] == "aprovado"
