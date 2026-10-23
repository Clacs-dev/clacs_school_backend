"""Bloco F: transporte escolar (viaturas, rotas, paragens, inscrições com lotação e cobrança, portal).
Corre antes de test_completo.py; a inscrição de teste é cancelada no fim."""
from tests.conftest import login

SENHA = "senha-de-teste-123"


def _ok(r, *codigos):
    assert r.status_code in (codigos or (200, 201, 204)), f"{r.request.method} {r.request.url.path}: {r.status_code} {r.text}"
    return r.json() if r.content and r.headers.get("content-type", "").startswith("application/json") else r


def test_transporte_completo(client, admin):
    ee = login(client, "encarregado@clacs.ao", SENHA)
    educando = client.get("/api/portal/eu", headers=ee).json()["educandos"][0]["id"]
    outro = next(a["id"] for a in client.get("/api/alunos", headers=admin).json() if a["turma_id"] and a["id"] != educando)

    servico = _ok(client.post("/api/servicos", headers=admin, json={
        "codigo": "TRANSP-T", "nome": "Transporte teste", "categoria": "transporte", "periodicidade": "mensal"}), 201)
    _ok(client.put("/api/precos", headers=admin, json={"servico_id": servico["id"], "valor": 12000}))
    viatura = _ok(client.post("/api/transporte/viaturas", headers=admin, json={
        "matricula": "LD-00-00-TT", "descricao": "Mini-autocarro", "lugares": 1, "motorista": "Sr. Kiala",
        "telefone_motorista": "+244923000777"}), 201)
    rota = _ok(client.post("/api/transporte/rotas", headers=admin, json={
        "nome": "Rota Teste", "viatura_id": viatura["id"], "servico_id": servico["id"], "hora_ida": "06:30:00",
        "paragens": [{"nome": "Paragem A", "ordem": 1, "hora_ida": "06:40:00", "hora_volta": "17:10:00"},
                     {"nome": "Paragem B", "ordem": 2}]}), 201)
    assert [p["nome"] for p in rota["paragens"]] == ["Paragem A", "Paragem B"] and rota["lugares_livres"] == 1
    pa = rota["paragens"][0]["id"]

    inscricao = _ok(client.post("/api/transporte/inscricoes", headers=admin, json={
        "aluno_id": educando, "rota_id": rota["id"], "paragem_id": pa}), 201)
    try:
        assert inscricao["adesao_id"] is not None  # entra nas cobranças mensais pelo serviço da rota
        r = client.post("/api/transporte/inscricoes", headers=admin, json={"aluno_id": outro, "rota_id": rota["id"]})
        assert r.status_code == 409 and "está cheia" in r.json()["detail"]
        r = client.post("/api/transporte/inscricoes", headers=admin, json={"aluno_id": educando, "rota_id": rota["id"]})
        assert r.status_code == 409 and "já está inscrito" in r.json()["detail"]
        outra_rota = _ok(client.post("/api/transporte/rotas", headers=admin, json={
            "nome": "Rota Sem Viatura", "paragens": [{"nome": "Longe", "ordem": 1}]}), 201)
        r = client.patch(f"/api/transporte/inscricoes/{inscricao['id']}", headers=admin,
                         json={"paragem_id": outra_rota["paragens"][0]["id"]})
        assert r.status_code == 422

        passageiros = _ok(client.get(f"/api/transporte/rotas/{rota['id']}/alunos", headers=admin))
        assert [(p["aluno_id"], p["paragem"]) for p in passageiros] == [(educando, "Paragem A")]
        portal = _ok(client.get(f"/api/portal/educandos/{educando}/transporte", headers=ee))
        assert (portal["rota"], portal["paragem"], portal["hora_ida"], portal["motorista"]) == \
            ("Rota Teste", "Paragem A", "06:40:00", "Sr. Kiala")

        # CRUD: ver, editar e apagar; uma viatura em uso não se apaga
        assert _ok(client.get(f"/api/transporte/rotas/{rota['id']}", headers=admin))["nome"] == "Rota Teste"
        assert _ok(client.patch(f"/api/transporte/viaturas/{viatura['id']}", headers=admin, json={"lugares": 2}))["lugares"] == 2
        assert client.delete(f"/api/transporte/viaturas/{viatura['id']}", headers=admin).status_code == 409
        assert client.delete(f"/api/transporte/paragens/{rota['paragens'][1]['id']}", headers=admin).status_code == 204
        prof = login(client, "professor@clacs.ao", SENHA)
        assert client.get("/api/transporte/rotas", headers=prof).status_code == 403
    finally:
        cancelada = _ok(client.patch(f"/api/transporte/inscricoes/{inscricao['id']}", headers=admin,
                                     json={"estado": "cancelada"}))
    assert cancelada["estado"] == "cancelada" and cancelada["data_fim"]
    adesao = next(a for a in client.get("/api/refeitorio/adesoes", headers=admin).json() if a["id"] == inscricao["adesao_id"])
    assert adesao["estado"] == "cancelada"  # deixa de ser cobrado
    assert client.get(f"/api/portal/educandos/{educando}/transporte", headers=ee).json() is None
    assert client.get(f"/api/transporte/rotas/{rota['id']}/alunos", headers=admin).json() == []
