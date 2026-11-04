"""Bloco F: admissão de alunos novos, da pré-inscrição no Google Forms às boas-vindas (as 10 etapas), com os papéis
de cada etapa (coordenação, Direcção, secretaria) e a formalização pela família no link sem conta.
Corre antes de test_completo.py, cujo último teste encerra o ano lectivo activo."""
from app.integracoes.email import CAIXA_LOCAL
from tests.conftest import login

FORMS = "/api/publico/candidaturas/google-forms"
SEGREDO = {"X-Clacs-Segredo": "segredo-formulario-testes"}
PNG = b"\x89PNG\r\n\x1a\nteste"
PDF = b"%PDF-1.4\n%teste\n"


def _ok(r, *codigos):
    assert r.status_code in (codigos or (200, 201, 204)), f"{r.request.method} {r.request.url.path}: {r.status_code} {r.text}"
    return r.json() if r.content and r.headers.get("content-type", "").startswith("application/json") else r


def _conta(client, admin, email, perfil):
    if not any(u["email"] == email for u in client.get("/api/utilizadores", headers=admin).json()):
        _ok(client.post("/api/utilizadores", headers=admin, json={"nome": f"Conta {perfil}", "email": email,
                                                                 "senha": "senha-candidaturas-1", "perfis": [perfil]}))
    return login(client, email, "senha-candidaturas-1")


def _turma_com_vaga(client, admin):
    return next(t for t in client.get("/api/turmas", headers=admin).json() if not t["cheia"])


def _resposta(classe, nome="Joana Mbala", email="marta.mbala@gmail.com", id_resposta="2_ABC"):
    return {"id_resposta": id_resposta, "respostas": {
        "Nome completo do aluno": nome, "Data de nascimento do aluno": "11/04/2019", "Sexo": "Feminino",
        "Classe pretendida": classe, "Nome do encarregado de educação": "Marta Mbala", "Parentesco": "Mãe",
        "Telefone / WhatsApp": "+244923555111", "E-mail": email, "Carimbo de data/hora": "2026/11/03 10:00"}}


def test_google_forms_exige_segredo_e_nao_duplica(client, admin):
    turma = _turma_com_vaga(client, admin)
    assert client.post(FORMS, json=_resposta(turma["classe"])).status_code == 401
    assert client.post(FORMS, json=_resposta(turma["classe"]), headers={"X-Clacs-Segredo": "errado"}).status_code == 401
    r = _ok(client.post(FORMS, json=_resposta(turma["classe"], id_resposta="dup-1"), headers=SEGREDO), 201)
    assert r["duplicada"] is False and r["avisos"] == []
    c = _ok(client.get(f"/api/candidaturas/{r['candidatura_id']}", headers=admin))
    assert (c["origem"], c["estado"], c["classe_id"], c["sexo"], c["parentesco"]) == \
        ("google_forms", "submetida", turma["classe_id"], "F", "mae")
    assert c["data_nascimento"] == "2019-04-11" and c["respostas"]["Classe pretendida"] == turma["classe"]
    # reenvio da mesma resposta, ou a mesma criança/contacto por outro caminho: não cria outra candidatura
    assert _ok(client.post(FORMS, json=_resposta(turma["classe"], id_resposta="dup-1"), headers=SEGREDO), 201)["duplicada"]
    assert _ok(client.post(FORMS, json=_resposta(turma["classe"], id_resposta="dup-2"), headers=SEGREDO), 201)["duplicada"]
    _ok(client.post(f"/api/candidaturas/{c['id']}/cancelar", headers=admin, json={"parecer": "Teste"}))
    # classe desconhecida: cria na mesma, com aviso; sem nome do aluno: recusa
    r = _ok(client.post(FORMS, json=_resposta("Universidade", nome="Rui Kiala", id_resposta="x-1"), headers=SEGREDO), 201)
    assert r["avisos"] and _ok(client.get(f"/api/candidaturas/{r['candidatura_id']}", headers=admin))["classe_id"] is None
    sem_nome = _resposta(turma["classe"], id_resposta="x-2")
    del sem_nome["respostas"]["Nome completo do aluno"]
    assert client.post(FORMS, json=sem_nome, headers=SEGREDO).status_code == 422


def test_percurso_completo_ate_as_boas_vindas(client, admin):
    turma = _turma_com_vaga(client, admin)
    coord = _conta(client, admin, "coordenacao@teste.ao", "coordenacao")
    secretaria = _conta(client, admin, "secretaria.cand@teste.ao", "secretaria")
    prof = login(client, "professor@clacs.ao", "senha-de-teste-123")
    assert client.get("/api/candidaturas", headers=prof).status_code == 403  # o professor não vê as candidaturas

    # 1-2. pré-inscrição pelo formulário; alerta para a coordenação
    cid = _ok(client.post(FORMS, json=_resposta(turma["classe"], nome="Ana Percurso", email="ana.p@teste.ao",
                                                id_resposta="perc-1"), headers=SEGREDO), 201)["candidatura_id"]
    alertas = [a for a in client.get("/api/alertas", headers=admin, params={"tipo": "candidatura"}).json()
               if a["dados"]["candidatura_id"] == cid]
    assert alertas[0]["dados"]["para"] == "coordenacao"
    url = f"/api/candidaturas/{cid}"

    # a etapa da coordenação não é da secretaria; a ordem é obrigatória (sem reunião não valida)
    assert client.post(f"{url}/reuniao", headers=secretaria, json={"reuniao_em": "2026-11-10T09:30:00"}).status_code == 403
    assert client.post(f"{url}/validar", headers=coord, json={}).status_code == 409

    # 3. reunião obrigatória (a família é avisada por e-mail) e avaliação
    antes = len(CAIXA_LOCAL)
    c = _ok(client.post(f"{url}/reuniao", headers=coord, json={"reuniao_em": "2026-11-10T09:30:00",
                                                               "reuniao_local": "Gabinete da coordenação"}))
    assert c["estado"] == "reuniao_marcada" and c["vagas"]["livres"] > 0
    assert any("ana.p@teste.ao" in m["To"] and "10/11/2026" in m.get_content() for m in CAIXA_LOCAL[antes:])
    _ok(client.post(f"{url}/avaliacao", headers=coord, json={"avaliacao": "Reunião com a mãe; lê com fluência."}))

    # 4. coordenação valida; 5. só a Direcção autoriza
    assert _ok(client.post(f"{url}/validar", headers=coord, json={"parecer": "Perfil adequado"}))["estado"] == "validada"
    assert client.post(f"{url}/autorizar", headers=coord, json={}).status_code == 403
    assert _ok(client.post(f"{url}/autorizar", headers=admin, json={}))["estado"] == "autorizada"

    # 6. secretaria comunica a vaga com o link
    c = _ok(client.post(f"{url}/comunicar", headers=secretaria, json={"mensagem": "Prazo: 20 de Novembro."}))
    token = c["link_formalizacao"].rsplit("/", 1)[1]
    assert c["estado"] == "vaga_comunicada" and "token" not in c

    # 7. família (sem conta) preenche e carrega; não entrega sem os obrigatórios
    pub = f"/api/publico/candidaturas/{token}"
    assert client.get("/api/publico/candidaturas/token-que-nao-existe-123456").status_code == 404
    p = _ok(client.get(pub))
    assert p["pode_editar"] and "BI do encarregado" in p["em_falta"] and "avaliacao" not in p
    assert client.post(f"{pub}/submeter").status_code == 422
    _ok(client.put(pub, json={"morada": "Talatona, Rua 12", "contacto_emergencia": "Marta (mãe)",
                              "telefone_emergencia": "+244923555111", "bi_encarregado": "BI-CAND-0001",
                              "bi_numero": "007654321LA045", "alergias": "Amendoim"}))
    for tipo, nome, conteudo, mime in (("cedula_bi_aluno", "cedula.pdf", PDF, "application/pdf"),
                                       ("fotografia", "foto.png", PNG, "image/png"),
                                       ("bi_encarregado", "bi.pdf", PDF, "application/pdf"),
                                       ("outro", "engano.pdf", PDF, "application/pdf")):
        p = _ok(client.post(f"{pub}/documentos", data={"tipo": tipo}, files={"ficheiro": (nome, conteudo, mime)}), 201)
    engano = next(d["id"] for d in p["documentos"] if d["nome"] == "engano.pdf")
    assert len(_ok(client.delete(f"{pub}/documentos/{engano}"))["documentos"]) == 3
    assert _ok(client.post(f"{pub}/submeter"))["estado"] == "documentos_entregues"
    assert client.put(pub, json={"morada": "outra"}).status_code == 409  # depois de entregue, já não altera

    # 8. secretaria pede correcção; a família volta a entregar; secretaria matricula
    _ok(client.post(f"{url}/correccao", headers=secretaria, json={"correccao": "A cédula está ilegível."}))
    assert _ok(client.get(pub))["correccao"] == "A cédula está ilegível."
    _ok(client.post(f"{pub}/submeter"))
    c = _ok(client.post(f"{url}/matricular", headers=secretaria, json={"turma_id": turma["id"]}))
    assert c["estado"] == "matriculada" and c["turma_id"] == turma["id"]

    # 9. dados do aluno disponíveis: aluno, documentos, encarregado e matrícula na turma
    aluno = _ok(client.get(f"/api/alunos/{c['aluno_id']}", headers=coord))
    assert aluno["nome_completo"] == "Ana Percurso" and aluno["alergias"] == "Amendoim" and aluno["foto_url"]
    assert any(m["aluno_id"] == c["aluno_id"] for m in client.get("/api/matriculas", headers=admin,
                                                                  params={"turma_id": turma["id"]}).json())
    assert _ok(client.get(f"/api/encarregados/{c['encarregado_id']}", headers=admin))["bi_numero"] == "BI-CAND-0001"
    abertos = [a for a in client.get("/api/alertas", headers=admin, params={"tipo": "candidatura"}).json()
               if a["dados"]["candidatura_id"] == cid and a["estado"] in ("novo", "visto")]
    assert [a["dados"]["para"] for a in abertos] == ["pedagogico"]

    # 10. boas-vindas e fim do percurso (histórico com todas as etapas)
    c = _ok(client.post(f"{url}/boas-vindas", headers=secretaria, json={"kit_entregue": True, "documentacao_entregue": True}))
    assert c["estado"] == "concluida" and c["integracao"]["kit_entregue"] is True
    etapas = [h["estado"] for h in c["historico"]]
    for e in ("submetida", "reuniao_marcada", "reuniao_realizada", "validada", "autorizada", "vaga_comunicada",
              "documentos_entregues", "matriculada", "concluida"):
        assert e in etapas
    assert _ok(client.get("/api/candidaturas/resumo", headers=admin))["por_estado"]["concluida"] >= 1


def test_rejeicao_registo_manual_e_importacao_csv(client, admin):
    turma = _turma_com_vaga(client, admin)
    c = _ok(client.post("/api/candidaturas", headers=admin, json={
        "nome_aluno": "Carlos Manual", "nome_encarregado": "Pedro Manual", "telefone": "+244911000999",
        "classe_id": turma["classe_id"]}), 201)
    assert c["origem"] == "secretaria" and c["nivel"]
    assert client.post("/api/candidaturas", headers=admin, json={"nome_aluno": "Sem Contacto",
                                                                 "nome_encarregado": "X Y"}).status_code == 422
    assert client.post(f"/api/candidaturas/{c['id']}/rejeitar", headers=admin, json={}).status_code == 422
    assert _ok(client.post(f"/api/candidaturas/{c['id']}/rejeitar", headers=admin,
                           json={"parecer": "Sem vaga para o perfil"}))["estado"] == "rejeitada"

    csv = ("Carimbo de data/hora,Nome completo do aluno,Data de nascimento,Classe pretendida,"
           "Nome do encarregado de educação,Telefone / WhatsApp\n"
           f"2026/11/01 09:00,Beatriz CSV,2018-02-03,{turma['classe']},Rosa CSV,+244922000111\n"
           f"2026/11/01 09:05,,2018-02-03,{turma['classe']},Sem Aluno,+244922000112\n").encode()
    r = _ok(client.post("/api/candidaturas/importar", headers=admin, files={"ficheiro": ("respostas.csv", csv, "text/csv")}))
    assert (r["criadas"], r["duplicadas"], [e["linha"] for e in r["erros"]]) == (1, 0, [3])
    r = _ok(client.post("/api/candidaturas/importar", headers=admin, files={"ficheiro": ("respostas.csv", csv, "text/csv")}))
    assert (r["criadas"], r["duplicadas"]) == (0, 1)
    assert any(x["nome_aluno"] == "Beatriz CSV" and x["origem"] == "importacao"
               for x in _ok(client.get("/api/candidaturas", headers=admin, params={"q": "beatriz"})))


def test_coordenador_do_nivel_e_configuracao_google_forms(client, admin):
    turma = _turma_com_vaga(client, admin)
    classe = next(c for c in client.get("/api/classes", headers=admin).json() if c["id"] == turma["classe_id"])
    outro = _conta(client, admin, "coordenacao2@teste.ao", "coordenacao")
    eu = next(u["id"] for u in client.get("/api/utilizadores", headers=admin).json() if u["email"] == "coordenacao@teste.ao")
    _ok(client.patch(f"/api/niveis/{classe['nivel_id']}", headers=admin, json={"coordenador_id": eu}))
    c = _ok(client.post("/api/candidaturas", headers=admin, json={
        "nome_aluno": "Nível Coordenado", "nome_encarregado": "Mãe Coordenada", "email": "nivel@teste.ao",
        "classe_id": turma["classe_id"]}), 201)
    detalhe = _ok(client.get(f"/api/candidaturas/{c['id']}", headers=admin))
    assert detalhe["coordenador"] == "Conta coordenacao"
    # o coordenador de outro nível não decide; o do nível sim, e vê-a em "os meus níveis"
    assert client.post(f"/api/candidaturas/{c['id']}/reuniao", headers=outro,
                       json={"reuniao_em": "2026-11-12T10:00:00"}).status_code == 403
    coord = login(client, "coordenacao@teste.ao", "senha-candidaturas-1")
    _ok(client.post(f"/api/candidaturas/{c['id']}/reuniao", headers=coord, json={"reuniao_em": "2026-11-12T10:00:00"}))
    assert any(x["id"] == c["id"] for x in client.get("/api/candidaturas", headers=coord,
                                                      params={"meus_niveis": True}).json())
    _ok(client.patch(f"/api/niveis/{classe['nivel_id']}", headers=admin, json={"coordenador_id": None}))

    cfg = _ok(client.get("/api/candidaturas/google-forms", headers=admin))
    assert cfg["segredo_configurado"] and "segredo-formulario-testes" in cfg["script"]
    assert cfg["script"].count("http://testserver/api/publico/candidaturas/google-forms") == 1
    assert "COLE_AQUI_O_SEGREDO" in _ok(client.get("/api/candidaturas/google-forms", headers=coord))["script"]
