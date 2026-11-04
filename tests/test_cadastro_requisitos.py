"""Bloco G: requisitos do levantamento da Talatona Kids — secretaria (pedidos, documentos, declarações, renovação),
saúde escolar, acordos e multas, orçamento, calendário, horários e aulas.
Corre antes de test_completo.py, cujo último teste encerra o ano lectivo activo."""
from datetime import date, timedelta

from tests.conftest import login

SENHA = "senha-de-teste-123"
PDF = b"%PDF-1.4\n%teste\n"


def _ok(r, *codigos):
    assert r.status_code in (codigos or (200, 201, 204)), f"{r.request.method} {r.request.url.path}: {r.status_code} {r.text}"
    return r.json() if r.content and r.headers.get("content-type", "").startswith("application/json") else r


def _ee(client):
    return login(client, "encarregado@clacs.ao", SENHA)


def _educando(client, ee):
    return client.get("/api/portal/eu", headers=ee).json()["educandos"][0]["id"]


def _conta(client, admin, email, perfil):
    if not any(u["email"] == email for u in client.get("/api/utilizadores", headers=admin).json()):
        _ok(client.post("/api/utilizadores", headers=admin, json={"nome": f"Conta {perfil}", "email": email,
                                                                 "senha": "senha-requisitos-1", "perfis": [perfil]}))
    return login(client, email, "senha-requisitos-1")


# ---------------------------------------------------------------- secretaria
def test_pedido_de_informacao_com_resposta_da_familia(client, admin):
    ee = _ee(client)
    p = _ok(client.post("/api/portal/pedidos", headers=ee, json={
        "tipo": "financeiro", "assunto": "Propina de Outubro", "mensagem": "Já paguei, mas aparece em dívida."}), 201)
    assert p["servico"] == "tesouraria" and p["estado_nome"] == "recebido" and p["prazo"] > str(date.today())
    assert client.post(f"/api/pedidos/{p['id']}/aprovar", headers=admin, json={}).status_code == 409  # não se aprova
    _ok(client.post(f"/api/pedidos/{p['id']}/analisar", headers=admin))
    r = _ok(client.post(f"/api/pedidos/{p['id']}/pedir-informacao", headers=admin, json={"mensagem": "Envie o talão."}))
    assert r["estado"] == "aguarda_informacao"
    _ok(client.post(f"/api/portal/pedidos/{p['id']}/anexos", headers=ee, files={"ficheiro": ("talao.pdf", PDF, "application/pdf")}), 201)
    r = _ok(client.post(f"/api/portal/pedidos/{p['id']}/responder", headers=ee, json={"mensagem": "Talão em anexo."}))
    assert r["estado"] == "em_analise" and r["dados"]["anexos"][0]["nome"] == "talao.pdf"
    r = _ok(client.post(f"/api/pedidos/{p['id']}/encaminhar", headers=admin, json={"servico": "secretaria", "nota": "teste"}))
    assert r["servico"] == "secretaria"
    assert client.post(f"/api/pedidos/{p['id']}/concluir", headers=admin, json={}).status_code == 422  # sem resposta
    r = _ok(client.post(f"/api/pedidos/{p['id']}/concluir", headers=admin, json={"resposta": "Pagamento confirmado."}))
    assert r["estado"] == "concluido"
    assert [h["autor"] for h in r["historico"]].count("familia") == 2
    assert any(p["id"] == x["id"] for x in client.get("/api/pedidos", headers=admin, params={"servico": "secretaria"}).json())


def test_declaracao_por_modelo_emitida_e_entregue(client, admin):
    ee = _ee(client)
    aid = _educando(client, ee)
    assert {m["codigo"] for m in client.get("/api/portal/declaracoes", headers=ee).json()} >= {"matricula", "frequencia"}
    bad = client.post("/api/modelos-declaracao", headers=admin, json={"codigo": "x1", "nome": "Má", "texto": "Olá {desconhecida}"})
    assert bad.status_code == 422
    _ok(client.post("/api/modelos-declaracao", headers=admin, json={
        "codigo": "abono", "nome": "Declaração para abono", "texto": "Declara-se que <b>{nome}</b> frequenta a {classe}."}), 201)
    p = _ok(client.post("/api/portal/pedidos", headers=ee, json={"tipo": "declaracao", "aluno_id": aid, "modelo": "abono",
                                                                 "finalidade": "Abono de família"}), 201)
    _ok(client.post(f"/api/pedidos/{p['id']}/aprovar", headers=admin, json={}))
    for url, h in ((f"/api/pedidos/{p['id']}/declaracao.pdf", admin), (f"/api/portal/pedidos/{p['id']}/declaracao.pdf", ee)):
        assert client.get(url, headers=h).content.startswith(b"%PDF")
    assert _ok(client.post(f"/api/pedidos/{p['id']}/concluir", headers=admin, json={}))["estado"] == "concluido"  # entregue
    assert client.get(f"/api/alunos/{aid}/declaracao.pdf", headers=admin, params={"modelo": "frequencia"}).content.startswith(b"%PDF")
    assert client.get(f"/api/alunos/{aid}/declaracao.pdf", headers=admin, params={"modelo": "nao_existe"}).status_code == 404


def test_documentos_exigidos_em_falta_entregues_e_validados(client, admin):
    ee = _ee(client)
    aid = _educando(client, ee)
    exigido = _ok(client.post("/api/documentos-exigidos", headers=admin, json={
        "tipo": "atestado_medico", "nome": "Atestado médico", "obrigatorio": True}), 201)
    falta = next(x for x in client.get("/api/documentos/em-falta", headers=admin).json() if x["aluno_id"] == aid)
    assert "atestado_medico" in [f["tipo"] for f in falta["em_falta"]]
    assert _ok(client.post("/api/documentos/em-falta/avisar", headers=admin, json={"aluno_ids": [aid]}))["alunos"] == 1
    d = _ok(client.post(f"/api/portal/educandos/{aid}/documentos", headers=ee, data={"tipo": "atestado_medico"},
                        files={"ficheiro": ("atestado.pdf", PDF, "application/pdf")}), 201)
    assert d["estado"] == "recebido"
    assert any(x["id"] == d["id"] for x in client.get("/api/documentos", headers=admin, params={"estado": "recebido"}).json())
    assert client.post(f"/api/alunos/{aid}/documentos/{d['id']}/validar", headers=admin,
                       json={"estado": "rejeitado"}).status_code == 422   # sem motivo
    _ok(client.post(f"/api/alunos/{aid}/documentos/{d['id']}/validar", headers=admin,
                    json={"estado": "rejeitado", "observacao": "Ilegível"}))
    em_falta = client.get(f"/api/portal/educandos/{aid}/documentos", headers=ee).json()["em_falta"]
    assert "atestado_medico" in [f["tipo"] for f in em_falta]   # rejeitado conta como em falta
    _ok(client.post(f"/api/alunos/{aid}/documentos/{d['id']}/validar", headers=admin, json={"estado": "aceite"}))
    assert "atestado_medico" not in [f["tipo"] for f in client.get(f"/api/portal/educandos/{aid}/documentos", headers=ee).json()["em_falta"]]
    _ok(client.patch(f"/api/documentos-exigidos/{exigido['id']}", headers=admin, json={"activo": False}))
    painel = _ok(client.get("/api/secretaria/painel", headers=admin))
    assert painel["pedidos_abertos"] >= 0 and "secretaria" in painel["pedidos_por_servico"] or painel["pedidos_abertos"] == 0


def test_renovacao_com_ficha_confirmada_sem_apagar_contactos(client, admin):
    ee = _ee(client)
    aid = _educando(client, ee)
    ficha = _ok(client.get(f"/api/portal/educandos/{aid}/ficha", headers=ee))
    eid = ficha["contactos"][0]["encarregado_id"]
    antes = len(ficha["contactos"])
    assert any(x["aluno_id"] == aid for x in client.get("/api/contactos/avisos", headers=admin).json()) or ficha["avisos"] == []
    m = next(x for x in client.get("/api/matriculas", headers=admin).json() if x["aluno_id"] == aid and x["estado"] == "activa")
    turma = next(t for t in client.get("/api/turmas", headers=admin).json() if t["id"] == m["turma_id"])
    p = _ok(client.post("/api/portal/pedidos", headers=ee, json={
        "tipo": "matricula", "aluno_id": aid, "classe_id": turma["classe_id"], "ficha": {
            "morada": "Kilamba, Bloco Q", "contactos": [
                {"encarregado_id": eid, "email": "novo.email@teste.ao"},
                {"nome_completo": "Pai Renovação", "parentesco": "pai", "telefone": "+244912000333"}]}}), 201)
    # ainda matriculado no ano activo: a aprovação recusa, mas a ficha fica guardada no pedido para o próximo ano
    assert client.post(f"/api/pedidos/{p['id']}/aprovar", headers=admin, json={}).status_code == 409
    from app.database import SessionLocal
    from app.models import Aluno
    from app.services import secretaria
    with SessionLocal() as db:
        mudou = secretaria.aplicar_ficha(db, db.get(Aluno, aid), p["dados"]["ficha"])
        db.commit()
    assert "aluno.morada" in mudou and any("Pai Renovação" in x for x in mudou)
    depois = _ok(client.get(f"/api/alunos/{aid}/ficha", headers=admin))
    assert len(depois["contactos"]) == antes + 1 and depois["morada"] == "Kilamba, Bloco Q"
    assert next(c for c in depois["contactos"] if c["encarregado_id"] == eid)["email"] == "novo.email@teste.ao"
    _ok(client.post(f"/api/portal/pedidos/{p['id']}/cancelar", headers=ee))


def test_justificacao_guarda_o_registo_original(client, admin):
    ee = _ee(client)
    aid = _educando(client, ee)
    m = next(x for x in client.get("/api/matriculas", headers=admin).json() if x["aluno_id"] == aid and x["estado"] == "activa")
    dia = (date.today() - timedelta(days=3)).isoformat()
    _ok(client.post("/api/assiduidade", headers=admin, json={"turma_id": m["turma_id"], "data": dia,
                                                            "registos": [{"aluno_id": aid, "estado": "falta_injustificada"}]}))
    p = _ok(client.post("/api/portal/pedidos", headers=ee, json={"tipo": "justificacao_falta", "aluno_id": aid,
                                                                 "data": dia, "motivo": "Consulta médica"}), 201)
    _ok(client.post(f"/api/portal/pedidos/{p['id']}/anexos", headers=ee, files={"ficheiro": ("atestado.pdf", PDF, "application/pdf")}), 201)
    _ok(client.post(f"/api/pedidos/{p['id']}/aprovar", headers=admin, json={}))
    r = next(a for a in client.get("/api/assiduidade", headers=admin, params={"aluno_id": aid, "desde": dia, "ate": dia}).json())
    assert (r["estado"], r["estado_original"], r["pedido_id"]) == ("falta_justificada", "falta_injustificada", p["id"])


# ---------------------------------------------------------------- saúde
def test_saude_ocorrencia_medicacao_e_acesso_restrito(client, admin):
    ee = _ee(client)
    aid = _educando(client, ee)
    prof = login(client, "professor@clacs.ao", SENHA)
    secretaria = _conta(client, admin, "secretaria.req@teste.ao", "secretaria")
    o = _ok(client.post("/api/saude/ocorrencias", headers=prof, json={
        "aluno_id": aid, "data_hora": "2026-11-04T10:15:00+01:00", "local": "Recreio", "descricao": "Caiu no escorrega",
        "partilhada": True}), 201)
    assert client.get("/api/saude/ocorrencias", headers=prof).status_code == 403        # regista mas não consulta
    assert client.get("/api/saude/ocorrencias", headers=secretaria).status_code == 403  # sem acesso à saúde
    _ok(client.post(f"/api/saude/ocorrencias/{o['id']}/notas", headers=admin, json={
        "tipo": "contacto_familia", "texto": "Mãe informada", "canal": "telefone", "contactado": "Mãe",
        "orientacao": "Fica na escola"}), 201)
    o = _ok(client.patch(f"/api/saude/ocorrencias/{o['id']}", headers=admin, json={"medidas": "Gelo 10 minutos"}))
    assert [n["tipo"] for n in o["notas"]] == ["contacto_familia", "alteracao"]
    o = _ok(client.post(f"/api/saude/ocorrencias/{o['id']}/encerrar", headers=admin, json={"desfecho": "ficou_na_escola"}))
    assert o["estado"] == "encerrada"
    assert client.patch(f"/api/saude/ocorrencias/{o['id']}", headers=admin, json={"local": "Sala"}).status_code == 409
    assert any(x["id"] == o["id"] for x in client.get(f"/api/portal/educandos/{aid}/saude", headers=ee).json()["ocorrencias"])
    _ok(client.get(f"/api/saude/ocorrencias/{o['id']}", headers=admin))
    consultas = client.get("/api/auditoria", headers=admin, params={"tabela_": "ocorrencia_saude"}).json()
    assert any(a["accao"] == "view" and a["registo_id"] == o["id"] for a in consultas)  # cada consulta fica registada

    m = _ok(client.post(f"/api/portal/educandos/{aid}/medicacao", headers=ee, data={
        "medicamento": "Paracetamol", "dose": "5 ml", "horario": "12:30", "data_inicio": date.today().isoformat(),
        "confirmo": "true"}, files={"receita": ("receita.pdf", PDF, "application/pdf")}), 201)
    assert m["estado"] == "pendente" and m["receita_url"]
    assert client.post(f"/api/saude/medicacao/{m['id']}/administracoes", headers=admin, json={}).status_code == 409
    _ok(client.post(f"/api/saude/medicacao/{m['id']}/decidir", headers=admin, json={"autorizar": True}))
    assert _ok(client.get("/api/saude/painel", headers=admin))["medicacao_por_dar_hoje"] >= 1
    r = _ok(client.post(f"/api/saude/medicacao/{m['id']}/administracoes", headers=admin, json={"observacao": "Tomou bem"}), 201)
    assert r["administracoes"][0]["dose"] == "5 ml" and r["administracoes"][0]["por_nome"]
    p = _ok(client.post("/api/portal/pedidos", headers=ee, json={"tipo": "saude", "assunto": "Alergia nova",
                                                                 "mensagem": "Descobrimos alergia ao kiwi."}), 201)
    assert p["servico"] == "saude"


# ---------------------------------------------------------------- financeiro
def _cobranca_em_atraso(client, admin, aluno_id, codigo):
    s = _ok(client.post("/api/servicos", headers=admin, json={"codigo": codigo, "nome": f"Serviço {codigo}", "categoria": "outro"}), 201)
    venc = date.today() - timedelta(days=15)
    return s, _ok(client.post("/api/cobrancas", headers=admin, json={
        "aluno_id": aluno_id, "servico_id": s["id"], "competencia": venc.replace(day=1).isoformat(),
        "valor_base": 10000, "vencimento": venc.isoformat()}), 201)


def test_multas_por_escaloes_memoria_e_isencao(client, admin):
    aid = _educando(client, _ee(client))
    s, c = _cobranca_em_atraso(client, admin, aid, "MULTA1")
    _ok(client.post("/api/multas", headers=admin, json={"servico_id": s["id"], "tipo": "percentual", "valor": 1,
                                                       "escaloes": [{"desde_dias": 1, "percentual": 5},
                                                                    {"desde_dias": 11, "percentual": 10}]}), 201)
    _ok(client.post("/api/cobrancas/aplicar-multas", headers=admin))
    _ok(client.post("/api/cobrancas/aplicar-multas", headers=admin))   # recalcular não duplica
    from app.database import SessionLocal
    from app.models import Cobranca
    with SessionLocal() as db:
        x = db.get(Cobranca, c["id"])
        assert float(x.valor_multa) == 1000 and x.multa_calculo["percentual"] == 10 and x.multa_calculo["dias_atraso"] == 15
    secretaria = _conta(client, admin, "secretaria.req@teste.ao", "secretaria")
    assert client.post(f"/api/cobrancas/{c['id']}/isentar-multa", headers=secretaria, json={"motivo": "Erro do banco"}).status_code == 403
    r = _ok(client.post(f"/api/cobrancas/{c['id']}/isentar-multa", headers=admin, json={"motivo": "Erro do banco confirmado"}))
    assert r["valor_multa"] == 0 and r["multa_isenta"]
    _ok(client.post("/api/cobrancas/aplicar-multas", headers=admin))
    with SessionLocal() as db:
        assert float(db.get(Cobranca, c["id"]).valor_multa) == 0


def test_acordo_de_pagamento_suspende_multas_e_fica_cumprido(client, admin):
    ee = _ee(client)
    aid = _educando(client, ee)
    s, c = _cobranca_em_atraso(client, admin, aid, "ACORDO1")
    _ok(client.post("/api/multas", headers=admin, json={"servico_id": s["id"], "tipo": "percentual", "valor": 10}), 201)
    eid = client.get("/api/portal/eu", headers=ee).json()["encarregado"]["id"]
    modelo = _ok(client.post("/api/acordos-modelos", headers=admin, json={"nome": "Duas prestações", "num_prestacoes": 2}), 201)
    a = _ok(client.post("/api/acordos", headers=admin, json={
        "encarregado_id": eid, "cobranca_ids": [c["id"]], "motivo": "Dificuldade temporária", "modelo_id": modelo["id"],
        "primeiro_vencimento": (date.today() - timedelta(days=1)).isoformat()}), 201)
    assert a["estado"] == "proposto" and [p["valor"] for p in a["prestacoes"]] == [5000, 5000]
    assert client.post("/api/acordos", headers=admin, json={"encarregado_id": eid, "cobranca_ids": [c["id"]], "motivo": "outra vez",
                                                           "num_prestacoes": 2, "primeiro_vencimento": "2026-12-01"}).status_code == 409
    a = _ok(client.post(f"/api/acordos/{a['id']}/aprovar", headers=admin))
    assert a["estado"] == "activo" and a["incumprimento"]   # a 1.ª prestação venceu ontem
    _ok(client.post("/api/cobrancas/aplicar-multas", headers=admin))
    from app.database import SessionLocal
    from app.models import Cobranca
    with SessionLocal() as db:
        assert float(db.get(Cobranca, c["id"]).valor_multa) == 0   # suspensa pelo acordo
    metodo = client.get("/api/metodos-pagamento", headers=admin).json()[0]["id"]
    _ok(client.post("/api/pagamentos", headers=admin, json={"metodo_id": metodo, "valor": 5000, "cobranca_ids": [c["id"]]}), 201)
    a = _ok(client.get(f"/api/acordos/{a['id']}", headers=admin))
    assert (a["pago"], a["saldo"], a["incumprimento"]) == (5000, 5000, False)
    _ok(client.post("/api/pagamentos", headers=admin, json={"metodo_id": metodo, "valor": 5000, "cobranca_ids": [c["id"]]}), 201)
    assert _ok(client.get(f"/api/acordos/{a['id']}", headers=admin))["estado"] == "cumprido"
    assert any(x["id"] == a["id"] for x in client.get("/api/portal/acordos", headers=ee).json())


def test_orcamento_previsto_versus_realizado(client, admin):
    ano = next(a for a in client.get("/api/anos-lectivos", headers=admin).json() if a["estado"] == "activo")
    o = _ok(client.post("/api/orcamentos", headers=admin, json={"ano_id": ano["id"], "designacao": "Orçamento de teste"}), 201)
    _ok(client.post(f"/api/orcamentos/{o['id']}/linhas", headers=admin, json={"tipo": "despesa", "categoria": "material", "valor": 100000}), 201)
    _ok(client.post(f"/api/orcamentos/{o['id']}/linhas", headers=admin, json={"tipo": "receita", "categoria": "propina", "valor": 500000}), 201)
    _ok(client.post("/api/despesas", headers=admin, json={"data": ano["data_inicio"], "categoria": "material",
                                                         "descricao": "Papel", "valor": 120000}), 201)
    e = _ok(client.get(f"/api/orcamentos/{o['id']}/execucao", headers=admin))
    material = next(x for x in e["categorias"] if x["categoria"] == "material")
    assert (material["previsto"], material["realizado"], material["desvio"]) == (100000, 120000, 20000)
    assert e["meses"] and e["despesa_realizada"] >= 120000
    assert any(x["categoria"] == "propina" and x["valor"] > 0 for x in client.get(f"/api/orcamentos/{o['id']}/previsao-receitas", headers=admin).json())
    assert client.get("/api/despesas/categorias", headers=admin).json()


# ---------------------------------------------------------------- calendário, horários e aulas
def test_horarios_aulas_substituicoes_e_feriados(client, admin):
    turma = client.get("/api/turmas", headers=admin).json()[0]
    disc = client.get("/api/disciplinas", headers=admin).json()[0]
    profs = client.get("/api/funcionarios", headers=admin).json()
    segunda = date.today() - timedelta(days=date.today().weekday() + 7)   # segunda-feira da semana passada
    base = {"turma_id": turma["id"], "disciplina_id": disc["id"], "funcionario_id": profs[0]["id"], "dia_semana": 1,
            "tempo": 7, "hora_inicio": "15:00:00", "hora_fim": "15:45:00", "valido_desde": (segunda - timedelta(days=14)).isoformat()}
    h = _ok(client.post("/api/horarios", headers=admin, json=base), 201)
    assert client.post("/api/horarios", headers=admin, json={**base, "tempo": 8}).status_code == 409   # turma ocupada
    aulas = _ok(client.get("/api/aulas", headers=admin, params={"data": segunda.isoformat(), "turma_id": turma["id"]}))
    assert any(a["horario_id"] == h["id"] for a in aulas)
    _ok(client.post("/api/substituicoes", headers=admin, json={"horario_id": h["id"], "data": segunda.isoformat(),
                                                              "substituto_id": profs[-1]["id"]}), 201)
    a = _ok(client.post("/api/aulas/registar", headers=admin, json={
        "horario_id": h["id"], "data": segunda.isoformat(), "sumario": "Revisões", "todos": "presente"}))
    assert a["funcionario_id"] == profs[-1]["id"] and a["presencas"] > 0
    rel = _ok(client.get("/api/assiduidade/relatorio", headers=admin, params={
        "turma_id": turma["id"], "desde": segunda.isoformat(), "ate": segunda.isoformat()}))
    assert any(x["disciplina_id"] == disc["id"] and x["presente"] >= 1 for x in rel)
    prof = _ok(client.get("/api/professores/assiduidade", headers=admin, params={
        "desde": segunda.isoformat(), "ate": segunda.isoformat()}))
    assert next(x for x in prof if x["funcionario_id"] == profs[-1]["id"])["substituicoes_feitas"] >= 1
    # alterar a partir de hoje guarda a versão anterior; um feriado tira as aulas do dia
    novo = _ok(client.post(f"/api/horarios/{h['id']}/alterar", headers=admin, json={"desde": date.today().isoformat(), "tempo": 9,
                                                                                   "hora_inicio": "16:00:00", "hora_fim": "16:45:00"}), 201)
    assert len([x for x in client.get("/api/horarios", headers=admin, params={"turma_id": turma["id"], "historico": True}).json()
                if x["id"] in (h["id"], novo["id"])]) == 2
    _ok(client.post("/api/eventos", headers=admin, json={"tipo": "feriado", "titulo": "Feriado de teste",
                                                        "data_inicio": segunda.isoformat()}), 201)
    assert _ok(client.get("/api/aulas", headers=admin, params={"data": segunda.isoformat()})) == []
    ee = _ee(client)
    assert any(e["titulo"] == "Feriado de teste" for e in client.get("/api/portal/calendario", headers=ee,
                                                                    params={"desde": segunda.isoformat()}).json())
    _ok(client.get(f"/api/portal/educandos/{_educando(client, ee)}/horario", headers=ee))


# ---------------------------------------------------------------- pré-inscrição pública no site
def test_pre_inscricao_publica_sem_conta(client, admin):
    classes = _ok(client.get("/api/publico/candidaturas/classes"))   # sem sessão
    corpo = {"nome_aluno": "Tomás Site", "classe_id": classes[0]["id"], "nome_encarregado": "Rita Site",
             "telefone": "+244923000777", "aceito": True}
    assert client.post("/api/publico/candidaturas", json={**corpo, "aceito": False}).status_code == 422
    assert client.post("/api/publico/candidaturas", json={**corpo, "site": "http://spam"}).status_code == 422
    r = _ok(client.post("/api/publico/candidaturas", json=corpo), 201)
    assert r["duplicada"] is False
    assert _ok(client.post("/api/publico/candidaturas", json=corpo), 201)["duplicada"] is True
    c = _ok(client.get(f"/api/candidaturas/{r['referencia']}", headers=admin))
    assert (c["origem"], c["estado"], c["classe_id"]) == ("site", "submetida", classes[0]["id"])
