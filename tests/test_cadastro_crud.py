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


# ---------------------------------------------------------------- escala de notas (0 a 10) e bloco C
def test_notas_de_0_a_10_configuravel(client, admin):
    turma = client.get("/api/turmas", headers=admin).json()[0]["id"]
    av = client.get("/api/avaliacoes", headers=admin, params={"turma_id": turma}).json()[0]
    aluno = client.get(f"/api/avaliacoes/{av['id']}/notas", headers=admin).json()[0]["aluno_id"]
    antes = next(n["valor"] for n in client.get(f"/api/avaliacoes/{av['id']}/notas", headers=admin).json()
                 if n["aluno_id"] == aluno)
    r = client.put(f"/api/avaliacoes/{av['id']}/notas", headers=admin, json={"notas": [{"aluno_id": aluno, "valor": 11}]})
    assert r.status_code == 422 and "A nota máxima é 10" in r.json()["detail"]
    pauta = _ok(client.get("/api/pautas", headers=admin, params={"turma_id": turma, "periodo_id": av["periodo_id"]}))
    assert (pauta["nota_maxima"], pauta["nota_minima"]) == (10, 5)
    _ok(client.put("/api/parametros/nota_maxima", headers=admin, json={"valor": 20}))
    try:
        _ok(client.put(f"/api/avaliacoes/{av['id']}/notas", headers=admin, json={"notas": [{"aluno_id": aluno, "valor": 15}]}))
    finally:
        _ok(client.put("/api/parametros/nota_maxima", headers=admin, json={"valor": 10}))
        _ok(client.put(f"/api/avaliacoes/{av['id']}/notas", headers=admin, json={"notas": [{"aluno_id": aluno, "valor": antes}]}))


def test_lista_de_alunos_com_trimestres_saude_e_sinal(client, admin):
    alunos = client.get("/api/alunos", headers=admin).json()
    aluno = next(a for a in alunos if a["turma_id"] and a["avaliacoes"][0]["media"] is not None)
    assert [p["periodo"] for p in aluno["avaliacoes"]] == ["1.º Trimestre", "2.º Trimestre", "3.º Trimestre"]
    assert aluno["necessidade_especial"] is False
    saude = {"necessidades_especiais": "Dislexia", "grupo_sanguineo": "O+", "condicoes_saude": "Asma",
             "medicacao": "Salbutamol", "contacto_emergencia": "Tia Rosa", "telefone_emergencia": "+244923000999"}
    _ok(client.patch(f"/api/alunos/{aluno['id']}", headers=admin, json=saude))
    try:
        linha = next(a for a in client.get("/api/alunos", headers=admin).json() if a["id"] == aluno["id"])
        ficha = _ok(client.get(f"/api/alunos/{aluno['id']}", headers=admin))
        assert linha["necessidade_especial"] and ficha["necessidade_especial"]
        assert {k: ficha[k] for k in saude} == saude
        assert client.patch(f"/api/alunos/{aluno['id']}", headers=admin, json={"grupo_sanguineo": "Z"}).status_code == 422
    finally:
        _ok(client.patch(f"/api/alunos/{aluno['id']}", headers=admin, json={"necessidades_especiais": None}))


def test_lista_de_alunos_no_pedagogico(client, admin):
    prof = login(client, "professor@clacs.ao", "senha-de-teste-123")
    minhas = {t["id"] for t in client.get("/api/turmas", headers=prof).json()}
    lista = _ok(client.get("/api/pedagogico/alunos", headers=prof))
    assert lista and {a["turma_id"] for a in lista} <= minhas
    outra = next(t["id"] for t in client.get("/api/turmas", headers=admin).json() if t["id"] not in minhas)
    assert client.get("/api/pedagogico/alunos", headers=prof, params={"turma_id": outra}).status_code == 403
    assert len(_ok(client.get("/api/pedagogico/alunos", headers=admin))) >= len(lista)


def test_lista_de_encarregados_com_filhos_e_contas(client, admin):
    ees = _ok(client.get("/api/encarregados", headers=admin))
    for e in ees:
        assert e["num_educandos"] == len(e["educandos"])
    com_divida = next(e for e in ees if e["total_em_divida"] > 0)
    linhas = client.get("/api/cobrancas", headers=admin, params={"encarregado_id": com_divida["id"]}).json()["linhas"]
    if linhas:  # o filtro por encarregado existe: os totais batem com as cobranças
        assert round(sum(x["valor_em_aberto"] for x in linhas), 2) == com_divida["total_em_divida"]


def test_portal_pauta_do_educando(client):
    ee = login(client, "encarregado@clacs.ao", "senha-de-teste-123")
    aid = client.get("/api/portal/eu", headers=ee).json()["educandos"][0]["id"]
    pauta = _ok(client.get(f"/api/portal/educandos/{aid}/pauta", headers=ee))
    assert pauta["periodos"] == ["1.º Trimestre", "2.º Trimestre", "3.º Trimestre"] and pauta["nota_maxima"] == 10
    assert pauta["linhas"] and all(len(ln["medias"]) == 3 for ln in pauta["linhas"])
    com_nota = [ln for ln in pauta["linhas"] if ln["media_final"] is not None]
    assert com_nota and all(ln["situacao"] in ("positiva", "negativa") for ln in com_nota)
    assert client.get("/api/portal/educandos/999999/pauta", headers=ee).status_code == 404
