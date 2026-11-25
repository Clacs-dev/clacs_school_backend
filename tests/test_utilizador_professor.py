"""Conta com perfil professor: tem de estar ligada ao funcionário (é por aí que vê as suas turmas e aulas)."""
from tests.conftest import login


def test_professor_exige_funcionario_e_ve_so_as_suas_turmas(client, admin):
    base = {"nome": "Prof. Sem Ligação", "email": "prof.ligacao@teste.ao", "senha": "professor-123", "perfis": ["professor"]}
    r = client.post("/api/utilizadores", headers=admin, json=base)
    assert r.status_code == 422 and "Funcionário associado" in r.json()["detail"]

    # um funcionário de outra escola (ou inexistente) também é recusado
    assert client.post("/api/utilizadores", headers=admin, json={**base, "funcionario_id": 999999}).status_code == 422

    prof = client.post("/api/funcionarios", headers=admin, json={"nome_completo": "Prof. Ligação Teste", "cargo": "professor"})
    assert prof.status_code == 201, prof.text
    fid = prof.json()["id"]
    r = client.post("/api/utilizadores", headers=admin, json={**base, "funcionario_id": fid})
    assert r.status_code == 201, r.text
    uid = r.json()["id"]
    assert r.json()["funcionario"] == "Prof. Ligação Teste"

    # ao editar: não se pode tirar a ligação a quem é professor; trocar de perfil liberta-a
    assert client.patch(f"/api/utilizadores/{uid}", headers=admin, json={"funcionario_id": None}).status_code == 422
    assert client.patch(f"/api/utilizadores/{uid}", headers=admin, json={"nome": "Prof. Ligação"}).status_code == 200

    # sem turmas atribuídas não vê nenhuma; depois de atribuída vê só essa
    h = login(client, "prof.ligacao@teste.ao", "professor-123")
    assert client.get("/api/turmas", headers=h).json() == []
    turmas = client.get("/api/turmas", headers=admin).json()
    assert turmas, "o seed deve ter turmas"
    t = turmas[0]
    disc = client.get(f"/api/classes/{t['classe_id']}/disciplinas", headers=admin).json()[0]
    r = client.put(f"/api/turmas/{t['id']}/atribuicoes", headers=admin, json={"disciplina_id": disc["id"], "funcionario_id": fid})
    assert r.status_code == 200, r.text
    assert [x["id"] for x in client.get("/api/turmas", headers=h).json()] == [t["id"]]


def test_ligar_conta_existente_ao_funcionario(client, admin):
    """Contas criadas antes desta regra (perfil secretaria → professor) ligam-se ao editar."""
    r = client.post("/api/utilizadores", headers=admin, json={
        "nome": "Futura Professora", "email": "futura.prof@teste.ao", "senha": "professor-123", "perfis": ["secretaria"]})
    uid = r.json()["id"]
    assert client.patch(f"/api/utilizadores/{uid}", headers=admin, json={"perfis": ["professor"]}).status_code == 422
    fid = client.post("/api/funcionarios", headers=admin, json={"nome_completo": "Futura Professora", "cargo": "professor"}).json()["id"]
    r = client.patch(f"/api/utilizadores/{uid}", headers=admin, json={"perfis": ["professor"], "funcionario_id": fid})
    assert r.status_code == 200 and r.json()["funcionario_id"] == fid
