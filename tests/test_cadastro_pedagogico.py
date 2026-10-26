"""Bloco D: pauta final e na horizontal, todos presentes / todos ausentes, calendário de provas e MAC no portal,
avaliação dos professores pelos encarregados. Corre antes de test_completo.py (que encerra o ano lectivo)."""
from datetime import date, timedelta

from tests.conftest import login

SENHA = "senha-de-teste-123"
PNG = bytes.fromhex("89504e470d0a1a0a") + b"imagem"


def _ok(r, *codigos):
    assert r.status_code in (codigos or (200, 201, 204)), f"{r.request.method} {r.request.url.path}: {r.status_code} {r.text}"
    return r.json() if r.content and r.headers.get("content-type", "").startswith("application/json") else r


def _educando(client, admin):
    ee = login(client, "encarregado@clacs.ao", SENHA)
    aid = client.get("/api/portal/eu", headers=ee).json()["educandos"][0]["id"]
    aluno = next(a for a in client.get("/api/alunos", headers=admin).json() if a["id"] == aid)
    return ee, aluno


def test_pauta_final_e_pdfs_na_horizontal(client, admin):
    turma = next(t for t in client.get("/api/turmas", headers=admin).json() if t["alunos"])
    pauta = _ok(client.get("/api/pautas/final", headers=admin, params={"turma_id": turma["id"]}))
    assert pauta["periodos"] == ["1.º Trimestre", "2.º Trimestre", "3.º Trimestre"] and pauta["nota_maxima"] == 10
    plano = {d["id"] for d in client.get(f"/api/classes/{turma['classe_id']}/disciplinas", headers=admin).json()}
    assert {d["id"] for d in pauta["disciplinas"]} == plano
    for ln in pauta["linhas"]:
        assert all(len(d["trimestres"]) == 3 for d in ln["disciplinas"])
        if ln["media_final"] is not None:
            assert ln["situacao"] == ("aprovado" if ln["media_final"] >= pauta["nota_minima"] else "reprovado")
    final = client.get(f"/api/turmas/{turma['id']}/pauta-final.pdf", headers=admin)
    periodo = client.get(f"/api/anos-lectivos/{turma['ano_id']}/periodos", headers=admin).json()[0]["id"]
    por_periodo = client.get(f"/api/turmas/{turma['id']}/pauta.pdf", headers=admin, params={"periodo_id": periodo})
    for pdf in (final, por_periodo):
        assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
        assert b"/MediaBox [ 0 0 841.8898 595.2756 ]" in pdf.content  # A4 na horizontal


def test_todos_presentes_e_todos_ausentes(client, admin):
    turma = next(t for t in client.get("/api/turmas", headers=admin).json() if t["alunos"] >= 2)
    alunos = [a["id"] for a in client.get("/api/alunos", headers=admin, params={"turma_id": turma["id"]}).json()]
    dia = (date.today() - timedelta(days=3)).isoformat()
    _ok(client.post("/api/assiduidade", headers=admin, json={
        "turma_id": turma["id"], "data": dia, "todos": "presente",
        "registos": [{"aluno_id": alunos[0], "estado": "atraso"}]}))
    registos = {r["aluno_id"]: r["estado"] for r in client.get("/api/assiduidade", headers=admin,
                                                                  params={"turma_id": turma["id"], "desde": dia, "ate": dia}).json()}
    assert set(registos) == set(alunos) and registos[alunos[0]] == "atraso"
    assert {registos[a] for a in alunos[1:]} == {"presente"}
    _ok(client.post("/api/assiduidade", headers=admin, json={"turma_id": turma["id"], "data": dia,
                                                              "todos": "falta_injustificada"}))
    estados = {r["estado"] for r in client.get("/api/assiduidade", headers=admin,
                                                params={"turma_id": turma["id"], "desde": dia, "ate": dia}).json()}
    assert estados == {"falta_injustificada"}
    assert client.post("/api/assiduidade", headers=admin, json={"turma_id": turma["id"], "data": dia}).status_code == 422


def test_portal_calendario_de_provas_e_mac(client, admin):
    ee, aluno = _educando(client, admin)
    disc = client.get(f"/api/alunos/{aluno['id']}/disciplinas", headers=admin).json()[0]["id"]
    ano = next(a for a in client.get("/api/anos-lectivos", headers=admin).json() if a["estado"] == "activo")
    periodo = client.get(f"/api/anos-lectivos/{ano['id']}/periodos", headers=admin).json()[0]["id"]
    tipo = client.get("/api/tipos-avaliacao", headers=admin).json()[0]["id"]
    daqui = (date.today() + timedelta(days=10)).isoformat()
    prova = _ok(client.post("/api/avaliacoes", headers=admin, json={
        "turma_id": aluno["turma_id"], "disciplina_id": disc, "periodo_id": periodo, "tipo_id": tipo,
        "data": daqui, "descricao": "Prova de leitura"}), 201)
    calendario = _ok(client.get(f"/api/portal/educandos/{aluno['id']}/calendario", headers=ee))
    assert any(c["avaliacao_id"] == prova["id"] and c["descricao"] == "Prova de leitura" for c in calendario)
    assert all(c["data"] >= date.today().isoformat() for c in calendario)

    mac = _ok(client.get(f"/api/portal/educandos/{aluno['id']}/mac", headers=ee))
    assert mac["periodos"] and mac["linhas"]
    assert any(t["mac"] is not None and t["provas"] is not None for ln in mac["linhas"] for t in ln["trimestres"])


def test_pais_avaliam_os_professores(client, admin):
    ee, aluno = _educando(client, admin)
    disc = client.get(f"/api/alunos/{aluno['id']}/disciplinas", headers=admin).json()[0]["id"]
    prof = _ok(client.post("/api/funcionarios", headers=admin, json={"nome_completo": "Prof. Avaliada", "cargo": "professor"}), 201)
    _ok(client.put(f"/api/turmas/{aluno['turma_id']}/atribuicoes", headers=admin,
                   json={"disciplina_id": disc, "funcionario_id": prof["id"]}))
    lista = _ok(client.get(f"/api/portal/educandos/{aluno['id']}/professores", headers=ee))
    assert any(p["funcionario_id"] == prof["id"] and p["minha_nota"] is None for p in lista)

    corpo = {"funcionario_id": prof["id"], "aluno_id": aluno["id"], "nota": 4, "comentario": "Atenciosa."}
    primeira = _ok(client.post("/api/portal/avaliacoes-professores", headers=ee, json=corpo))
    segunda = _ok(client.post("/api/portal/avaliacoes-professores", headers=ee, json={**corpo, "nota": 2}))
    assert segunda["id"] == primeira["id"] and segunda["nota"] == 2  # corrige, não duplica
    assert client.post("/api/portal/avaliacoes-professores", headers=ee, json={**corpo, "nota": 6}).status_code == 422
    outro = _ok(client.post("/api/funcionarios", headers=admin, json={"nome_completo": "Sem turma", "cargo": "professor"}), 201)
    r = client.post("/api/portal/avaliacoes-professores", headers=ee, json={**corpo, "funcionario_id": outro["id"]})
    assert r.status_code == 422
    meu = next(p for p in client.get(f"/api/portal/educandos/{aluno['id']}/professores", headers=ee).json()
               if p["funcionario_id"] == prof["id"])
    assert meu["minha_nota"] == 2

    resumo = next(r for r in _ok(client.get("/api/professores/avaliacoes", headers=admin))
                  if r["funcionario_id"] == prof["id"])
    assert (resumo["avaliacoes"], resumo["media"], resumo["distribuicao"]["2"]) == (1, 2, 1)
    assert resumo["comentarios"][0]["comentario"] == "Atenciosa." and "encarregado" not in resumo["comentarios"][0]


def test_nota_minima_fora_da_escala_passa_a_metade(client, admin):
    """O antigo nota_minima = 10 (escala de 0 a 20) com a escala nova de 0 a 10 dava toda a gente negativa."""
    turma = next(t for t in client.get("/api/turmas", headers=admin).json() if t["alunos"])
    _ok(client.put("/api/parametros/nota_minima", headers=admin, json={"valor": 10}))
    try:
        pauta = _ok(client.get("/api/pautas/final", headers=admin, params={"turma_id": turma["id"]}))
        assert (pauta["nota_maxima"], pauta["nota_minima"]) == (10, 5)
        _ok(client.put("/api/parametros/nota_minima", headers=admin, json={"valor": 6}))
        assert _ok(client.get("/api/pautas/final", headers=admin, params={"turma_id": turma["id"]}))["nota_minima"] == 6
    finally:
        _ok(client.put("/api/parametros/nota_minima", headers=admin, json={"valor": 5}))


def test_portal_lista_classes_e_trimestres(client):
    ee = login(client, "encarregado@clacs.ao", SENHA)
    assert [p["designacao"] for p in _ok(client.get("/api/portal/periodos", headers=ee))] == \
        ["1.º Trimestre", "2.º Trimestre", "3.º Trimestre"]
    assert _ok(client.get("/api/portal/classes", headers=ee))


def test_ocorrencia_com_imagens(client, admin):
    aluno = client.get("/api/alunos", headers=admin).json()[0]
    tipo = client.post("/api/tipos-ocorrencia", headers=admin, json={"designacao": "Dano material", "gravidade": "moderada"}).json()
    oc = client.post("/api/ocorrencias", headers=admin, json={"aluno_id": aluno["id"], "tipo_id": tipo["id"],
                                                            "descricao": "Partiu o vidro da janela."}).json()
    png = ("vidro.png", PNG, "image/png")
    r = client.post(f"/api/ocorrencias/{oc['id']}/imagens", headers=admin, files={"ficheiro": png})
    assert r.status_code == 201, r.text
    img = r.json()
    assert img["url"].startswith("/api/ficheiros/") and img["nome"] == "vidro.png"
    assert client.get(img["url"], headers=admin).status_code == 200
    # só imagens
    assert client.post(f"/api/ocorrencias/{oc['id']}/imagens", headers=admin,
                       files={"ficheiro": ("x.pdf", b"%PDF-1.4", "application/pdf")}).status_code == 415
    lista = client.get("/api/ocorrencias", headers=admin, params={"aluno_id": aluno["id"]}).json()
    assert [i["id"] for i in next(o for o in lista if o["id"] == oc["id"])["imagens"]] == [img["id"]]
    # no máximo seis por ocorrência
    for _ in range(5):
        assert client.post(f"/api/ocorrencias/{oc['id']}/imagens", headers=admin, files={"ficheiro": png}).status_code == 201
    assert client.post(f"/api/ocorrencias/{oc['id']}/imagens", headers=admin, files={"ficheiro": png}).status_code == 409
    # retirar uma imagem; outra ocorrência não serve de atalho
    assert client.delete(f"/api/ocorrencias/{oc['id']}/imagens/{img['id']}", headers=admin).status_code == 204
    assert client.delete(f"/api/ocorrencias/{oc['id']}/imagens/{img['id']}", headers=admin).status_code == 404
    # apagar a ocorrência leva as imagens
    assert client.delete(f"/api/ocorrencias/{oc['id']}", headers=admin).status_code == 204
