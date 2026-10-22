"""Bloco B: turmas cheias e a próxima turma com vaga, salas e funcionários, CRUD completo, disciplinas do aluno.

Corre depois de test_avancado.py e antes de test_completo.py (ordem alfabética). Os registos de teste ficam
numa classe própria, e as matrículas criadas são anuladas no fim para não mexerem nas cobranças dos outros testes.
"""
from tests.conftest import login


def _ok(r, *codigos):
    assert r.status_code in (codigos or (200, 201, 204)), f"{r.request.method} {r.request.url.path}: {r.status_code} {r.text}"
    return r.json() if r.content and r.headers.get("content-type", "").startswith("application/json") else None


def test_turma_cheia_sugere_a_proxima(client, admin):
    nivel = client.get("/api/niveis", headers=admin).json()[0]["id"]
    classe = _ok(client.post("/api/classes", headers=admin, json={"nivel_id": nivel, "designacao": "Classe Teste", "ordem": 99}))
    ta = _ok(client.post("/api/turmas", headers=admin, json={"designacao": "CT A", "classe_id": classe["id"], "capacidade": 1}))
    tb = _ok(client.post("/api/turmas", headers=admin, json={"designacao": "CT B", "classe_id": classe["id"], "capacidade": 1}))
    alunos = [_ok(client.post("/api/alunos", headers=admin, json={"nome_completo": f"Aluno Vaga {i}"}))["id"]
              for i in range(3)]
    mids = []
    try:
        # por classe: o sistema enche a A primeiro
        m = _ok(client.post("/api/matriculas", headers=admin, json={"aluno_id": alunos[0], "classe_id": classe["id"]}))
        assert m["turma_id"] == ta["id"]
        mids.append(m["id"])
        # a A está cheia: 409 que diz qual é a próxima, e a sugestão aponta para a B
        r = client.post("/api/matriculas", headers=admin, json={"aluno_id": alunos[1], "turma_id": ta["id"]})
        assert r.status_code == 409 and "CT A está cheia (1/1)" in r.json()["detail"] and "CT B" in r.json()["detail"]
        s = _ok(client.get("/api/turmas/sugestao", headers=admin, params={"turma_id": ta["id"]}))
        assert s["turma_pedida"]["cheia"] and s["sugerida"]["id"] == tb["id"] and "CT B" in s["mensagem"]
        m = _ok(client.post("/api/matriculas", headers=admin, json={"aluno_id": alunos[1], "classe_id": classe["id"]}))
        assert m["turma_id"] == tb["id"]
        mids.append(m["id"])
        # todas cheias: não há sugestão e a matrícula por classe é recusada
        assert _ok(client.get("/api/turmas/sugestao", headers=admin, params={"classe_id": classe["id"]}))["sugerida"] is None
        r = client.post("/api/matriculas", headers=admin, json={"aluno_id": alunos[2], "classe_id": classe["id"]})
        assert r.status_code == 409 and "Não há turma da Classe Teste com vaga" in r.json()["detail"]
        assert client.post("/api/matriculas", headers=admin, json={"aluno_id": alunos[2]}).status_code == 422
    finally:
        for mid in mids:
            client.post(f"/api/matriculas/{mid}/anular", headers=admin)


def test_capacidade_por_omissao_configuravel(client, admin):
    classe = client.get("/api/classes", headers=admin).json()[0]["id"]
    t = _ok(client.post("/api/turmas", headers=admin, json={"designacao": "Cap 7", "classe_id": classe}))
    assert t["capacidade"] == 7
    _ok(client.put("/api/parametros/capacidade_turma", headers=admin, json={"valor": 25}))
    try:
        t = _ok(client.post("/api/turmas", headers=admin, json={"designacao": "Cap 25", "classe_id": classe}))
        assert t["capacidade"] == 25
    finally:
        _ok(client.put("/api/parametros/capacidade_turma", headers=admin, json={"valor": 7}))


def test_sala_com_tipo_piso_e_crud(client, admin):
    sala = _ok(client.post("/api/salas", headers=admin, json={
        "designacao": "Lab Química", "tipo": "laboratorio", "piso": "1.º andar", "localizacao": "Bloco B", "capacidade": 20}))
    assert (sala["tipo"], sala["piso"], sala["localizacao"], sala["activo"]) == ("laboratorio", "1.º andar", "Bloco B", True)
    assert _ok(client.get(f"/api/salas/{sala['id']}", headers=admin))["designacao"] == "Lab Química"
    s = _ok(client.patch(f"/api/salas/{sala['id']}", headers=admin, json={"piso": "R/C", "tipo": "informatica"}))
    assert (s["piso"], s["tipo"], s["localizacao"]) == ("R/C", "informatica", "Bloco B")  # o resto fica
    assert client.patch(f"/api/salas/{sala['id']}", headers=admin, json={"tipo": "cozinha"}).status_code == 422
    assert client.post("/api/salas", headers=admin, json={"designacao": "Sala sem tipo"}).json()["tipo"] == "sala_aula"

    # uma sala usada por uma turma não se apaga; uma sala livre apaga-se
    classe = client.get("/api/classes", headers=admin).json()[0]["id"]
    _ok(client.post("/api/turmas", headers=admin, json={"designacao": "Turma do Lab", "classe_id": classe, "sala_id": sala["id"]}))
    r = client.delete(f"/api/salas/{sala['id']}", headers=admin)
    assert r.status_code == 409 and "Desactive" in r.json()["detail"]
    livre = _ok(client.post("/api/salas", headers=admin, json={"designacao": "Sala para apagar"}))
    assert client.delete(f"/api/salas/{livre['id']}", headers=admin).status_code == 204
    assert client.get(f"/api/salas/{livre['id']}", headers=admin).status_code == 404


def test_funcionario_com_ano_de_contratacao(client, admin):
    f = _ok(client.post("/api/funcionarios", headers=admin, json={
        "nome_completo": "Rita Contratada", "cargo": "professor", "ano_contratacao": 2021}))
    assert f["ano_contratacao"] == 2021
    assert _ok(client.patch(f"/api/funcionarios/{f['id']}", headers=admin, json={"ano_contratacao": 2022}))["ano_contratacao"] == 2022
    assert client.post("/api/funcionarios", headers=admin, json={
        "nome_completo": "X", "cargo": "professor", "ano_contratacao": 1800}).status_code == 422
    assert _ok(client.get(f"/api/funcionarios/{f['id']}", headers=admin))["nome_completo"] == "Rita Contratada"
    assert client.delete(f"/api/funcionarios/{f['id']}", headers=admin).status_code == 204


def test_crud_protege_referencias_e_permissoes(client, admin):
    disc = _ok(client.post("/api/disciplinas", headers=admin, json={"nome": "Disciplina CRUD", "sigla": "DC"}))
    assert _ok(client.patch(f"/api/disciplinas/{disc['id']}", headers=admin, json={"sigla": "DCR"}))["sigla"] == "DCR"
    classe = client.get("/api/classes", headers=admin).json()[0]
    assert client.patch(f"/api/classes/{classe['id']}", headers=admin, json={"nivel_id": 999999}).status_code == 422
    usada = client.get(f"/api/classes/{classe['id']}/disciplinas", headers=admin).json()[0]["id"]
    assert client.delete(f"/api/disciplinas/{usada}", headers=admin).status_code == 409  # está no plano curricular
    assert client.delete(f"/api/disciplinas/{disc['id']}", headers=admin).status_code == 204
    assert client.get("/api/niveis/999999", headers=admin).status_code == 404

    # o professor não apaga (sem pedagogico/apagar) nem edita avaliações de turmas onde não dá aulas
    prof = login(client, "professor@clacs.ao", "senha-de-teste-123")
    minhas = {t["id"] for t in client.get("/api/turmas", headers=prof).json()}
    outras = [t["id"] for t in client.get("/api/turmas", headers=admin).json() if t["id"] not in minhas]
    outra = next(a for tid in outras for a in client.get("/api/avaliacoes", headers=admin, params={"turma_id": tid}).json())
    assert client.patch(f"/api/avaliacoes/{outra['id']}", headers=prof, json={"descricao": "x"}).status_code == 403
    assert client.delete(f"/api/avaliacoes/{outra['id']}", headers=prof).status_code == 403


def test_disciplinas_do_aluno_pelo_plano_curricular(client, admin):
    com_professor = {t["id"] for t in client.get("/api/turmas", headers=admin).json()
                     if client.get(f"/api/turmas/{t['id']}/atribuicoes", headers=admin).json()}
    aluno = next(a for a in client.get("/api/alunos", headers=admin).json() if a["turma_id"] in com_professor)
    turma = next(t for t in client.get("/api/turmas", headers=admin).json() if t["id"] == aluno["turma_id"])
    plano = {d["id"] for d in client.get(f"/api/classes/{turma['classe_id']}/disciplinas", headers=admin).json()}
    disciplinas = _ok(client.get(f"/api/alunos/{aluno['id']}/disciplinas", headers=admin))
    assert {d["id"] for d in disciplinas} == plano and plano
    assert any(d["professor"] for d in disciplinas)  # com o professor atribuído na turma
    assert {d["id"] for d in _ok(client.get(f"/api/alunos/{aluno['id']}", headers=admin))["disciplinas"]} == plano
