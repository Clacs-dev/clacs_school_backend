"""Contrato da API: todas as rotas documentadas e chamadas com sucesso pelo menos uma vez.

Os modelos de resposta (app/respostas.py) correm em modo estrito nos testes, por isso cada chamada aqui confirma que
a resposta real tem exactamente os campos documentados no /api/docs. Corre depois de test_api.py e antes de
test_avancado.py / test_completo.py; usa os seus próprios registos para não mexer nos dados que esses testes usam.
"""
from datetime import date

import pyotp
from fastapi.routing import APIRoute
from pydantic import TypeAdapter, ValidationError

from app import schemas as S
from app.config import get_settings
from app.main import EXEMPLOS_RESPOSTA, app
from tests.conftest import login

PDF = b"%PDF-1.4\n%teste\n"


def ok(r, *codigos):
    esperado = codigos or (200, 201, 204)
    assert r.status_code in esperado, f"{r.request.method} {r.request.url.path} -> {r.status_code}: {r.text[:400]}"
    return r.json() if r.status_code != 204 and r.headers.get("content-type", "").startswith("application/json") else r


def test_todas_as_rotas_tem_resposta_documentada():
    sem = [f"{sorted(r.methods)[0]} {r.path}" for r in app.routes
           if isinstance(r, APIRoute) and r.path != "/" and r.status_code != 204
           and r.response_model is None and not r.responses]
    assert sem == []
    esquema = app.openapi()
    assert "LinhaCobranca" in esquema["components"]["schemas"]


def test_exemplos_de_pedido():
    """Cada corpo de pedido (JSON ou formulário) tem exemplo no /api/docs e o exemplo é válido."""
    esquema = app.openapi()
    componentes = esquema["components"]["schemas"]
    sem = []
    for caminho, operacoes in esquema["paths"].items():
        for metodo, op in operacoes.items():
            for tipo, conteudo in op.get("requestBody", {}).get("content", {}).items():
                if tipo == "application/json":
                    alvo = componentes.get(conteudo["schema"].get("$ref", "").split("/")[-1], {})
                    if "examples" not in alvo and "example" not in conteudo:
                        sem.append(f"{metodo.upper()} {caminho}")
                else:  # formulário: cada campo de texto com exemplo, cada ficheiro com descrição
                    campos = componentes[conteudo["schema"]["$ref"].split("/")[-1]]["properties"]
                    sem += [f"{metodo.upper()} {caminho} [{nome}]" for nome, c in campos.items()
                            if "examples" not in c and "description" not in c]
    assert sem == []
    for modelo, exemplo in S.EXEMPLOS.items():
        modelo.model_validate(exemplo)


def test_exemplos_de_resposta():
    """Todas as respostas JSON têm um exemplo real (scripts/gerar_exemplos.py) que cumpre o modelo documentado."""
    rotas = {f"{m} {r.path}": r for r in app.routes if isinstance(r, APIRoute) and r.response_model is not None
             for m in r.methods}
    assert sorted(set(rotas) - set(EXEMPLOS_RESPOSTA)) == [], "rotas sem exemplo; corra python -m scripts.gerar_exemplos"
    for chave, exemplo in EXEMPLOS_RESPOSTA.items():
        assert chave in rotas, f"{chave}: a rota já não existe; corra python -m scripts.gerar_exemplos"
        try:
            TypeAdapter(rotas[chave].response_model).validate_python(exemplo)
        except ValidationError as erro:
            raise AssertionError(f"{chave} desactualizado; corra python -m scripts.gerar_exemplos\n{erro}") from None


def test_consultas(client, admin):
    """Rotas GET simples (sem identificador no caminho)."""
    for url in ["/api/agendador", "/api/alertas", "/api/assiduidade", "/api/auditoria", "/api/cobrancas/devedores",
                "/api/comunicados", "/api/descontos", "/api/disciplinas", "/api/estabelecimento", "/api/funcionarios",
                "/api/importacao/lotes", "/api/importacao/modelos", "/api/indicadores/catalogo", "/api/modelos-mensagem",
                "/api/multas", "/api/niveis", "/api/ocorrencias", "/api/parametros", "/api/perfis",
                "/api/planos-pagamento", "/api/precos", "/api/primavera", "/api/primavera/documentos",
                "/api/refeitorio/adesoes", "/api/refeitorio/ementas", "/api/refeitorio/registos", "/api/regras",
                "/api/relatorios/agendados", "/api/salas", "/api/tipos-avaliacao", "/api/tipos-ocorrencia",
                "/api/utilizadores"]:
        ok(client.get(url, headers=admin))
    for url in ["/api/relatorios/devedores", "/api/relatorios/cobranca-turma"]:
        assert ok(client.get(url, headers=admin)).content[:2] == b"PK"


def test_academico(client, admin):
    ok(client.patch("/api/estabelecimento", headers=admin, json={"telefone": "+244 222 000 000"}))
    ok(client.put("/api/parametros/teste_contrato", headers=admin, json={"valor": {"a": 1}, "descricao": "teste"}))
    anos = ok(client.get("/api/anos-lectivos", headers=admin))
    antigo = next(a for a in anos if a["estado"] == "encerrado")
    ok(client.post(f"/api/anos-lectivos/{antigo['id']}/periodos", headers=admin,
                   json={"designacao": "Exame especial", "ordem": 4, "data_inicio": antigo["data_fim"],
                         "data_fim": antigo["data_fim"]}))
    nivel = ok(client.post("/api/niveis", headers=admin, json={"nome": "II Ciclo", "ordem": 3}))
    classe = ok(client.post("/api/classes", headers=admin, json={"nivel_id": nivel["id"], "designacao": "10.ª Classe", "ordem": 10}))
    disc = ok(client.post("/api/disciplinas", headers=admin, json={"nome": "Filosofia", "sigla": "FIL"}))
    ok(client.post(f"/api/classes/{classe['id']}/disciplinas", headers=admin, json={"disciplina_id": disc["id"], "carga_horaria": 2}))
    assert ok(client.get(f"/api/classes/{classe['id']}/disciplinas", headers=admin))[0]["carga_horaria"] == 2
    sala = ok(client.post("/api/salas", headers=admin, json={"designacao": "Laboratório", "capacidade": 20}))
    prof = ok(client.post("/api/funcionarios", headers=admin, json={"nome_completo": "Prof. Contrato", "cargo": "professor"}))
    ok(client.patch(f"/api/funcionarios/{prof['id']}", headers=admin, json={"telefone": "+244 923 111 222"}))
    turma = ok(client.post("/api/turmas", headers=admin, json={"designacao": "10.ª A", "classe_id": classe["id"],
                                                              "sala_id": sala["id"], "capacidade": 25}))
    ok(client.patch(f"/api/turmas/{turma['id']}", headers=admin, json={"capacidade": 30, "director_turma_id": prof["id"]}))
    ok(client.put(f"/api/turmas/{turma['id']}/atribuicoes", headers=admin,
                  json={"disciplina_id": disc["id"], "funcionario_id": prof["id"]}))
    assert ok(client.get(f"/api/turmas/{turma['id']}/atribuicoes", headers=admin))[0]["professor"] == "Prof. Contrato"


def test_cadastro_e_financeiro(client, admin):
    com_vagas = [t for t in ok(client.get("/api/turmas", headers=admin)) if not t["cheia"]]
    a, b = com_vagas[0], com_vagas[1]
    ee = ok(client.post("/api/encarregados", headers=admin, json={"nome_completo": "Encarregado Contrato", "telefone": "923999000"}))
    ok(client.patch(f"/api/encarregados/{ee['id']}", headers=admin, json={"email": "contrato@teste.ao"}))
    ok(client.put(f"/api/encarregados/{ee['id']}/preferencias", headers=admin, json={"canal": "sms", "activo": False}))
    aluno = ok(client.post("/api/alunos", headers=admin, json={"nome_completo": "Aluno Contrato", "turma_id": a["id"],
                                                               "encarregado_id": ee["id"]}))
    ok(client.patch(f"/api/alunos/{aluno['id']}", headers=admin, json={"alergias": "Nenhuma"}))
    ok(client.post(f"/api/alunos/{aluno['id']}/documentos", headers=admin, json={"tipo": "BI", "ficheiro_url": "https://x/bi.pdf"}))
    assert ok(client.get(f"/api/encarregados/{ee['id']}", headers=admin))["preferencias"]["sms"] is False
    mat = ok(client.get("/api/matriculas", headers=admin, params={"turma_id": a["id"]}))
    mid = next(m["id"] for m in mat if m["aluno_id"] == aluno["id"])
    ok(client.post(f"/api/matriculas/{mid}/turma", headers=admin, json={"turma_id": b["id"], "motivo": "pedido do EE"}))

    servico = ok(client.post("/api/servicos", headers=admin, json={"codigo": "TRANSP", "nome": "Transporte",
                                                                   "categoria": "transporte"}))
    ok(client.patch(f"/api/servicos/{servico['id']}", headers=admin, json={"codigo": "TRANSP", "nome": "Transporte escolar",
                                                                           "categoria": "transporte"}))
    ok(client.put("/api/precos", headers=admin, json={"servico_id": servico["id"], "valor": 12000}))
    ok(client.post("/api/planos-pagamento", headers=admin, json={"designacao": "Trimestral", "num_prestacoes": 3,
                                                                 "mes_inicio": 9, "dia_vencimento": 5}))
    ok(client.post("/api/multas", headers=admin, json={"servico_id": servico["id"], "dias_tolerancia": 5,
                                                       "tipo": "fixo", "valor": 1000}))
    desc = ok(client.post("/api/descontos", headers=admin, json={"designacao": "Bolsa", "origem": "bolsa",
                                                                 "tipo": "percentual", "valor": 50}))
    ok(client.post(f"/api/matriculas/{mid}/descontos", headers=admin, json={"desconto_id": desc["id"]}))
    assert ok(client.get(f"/api/matriculas/{mid}/descontos", headers=admin))[0]["desconto"]["designacao"] == "Bolsa"
    ok(client.post("/api/metodos-pagamento", headers=admin, json={"nome": "Cheque", "tipo": "deposito"}))
    cob = ok(client.post("/api/cobrancas", headers=admin, json={"aluno_id": aluno["id"], "servico_id": servico["id"],
                                                               "competencia": date.today().replace(day=1).isoformat()}))
    ok(client.post(f"/api/cobrancas/{cob['id']}/anular", headers=admin))
    ok(client.post("/api/cobrancas/aplicar-multas", headers=admin))
    pg = ok(client.get("/api/pagamentos", headers=admin))[0]
    ok(client.post(f"/api/pagamentos/{pg['id']}/comprovativo", headers=admin,
                   files={"ficheiro": ("talao.pdf", PDF, "application/pdf")}))
    # refeitório (aluno novo, para não mexer nos alunos de demonstração)
    alim = next(s for s in ok(client.get("/api/servicos", headers=admin)) if s["codigo"] == "ALIM")
    ad = ok(client.post("/api/refeitorio/adesoes", headers=admin, json={"aluno_id": aluno["id"], "servico_id": alim["id"]}))
    ok(client.patch(f"/api/refeitorio/adesoes/{ad['id']}", headers=admin, json={"modalidade": "alimentacao_completa"}))
    tipo = ok(client.post("/api/refeitorio/tipos", headers=admin, json={"designacao": "Jantar", "hora_inicio": "19:00:00"}))
    ok(client.put("/api/refeitorio/ementas", headers=admin, json={"refeicao_tipo_id": tipo["id"],
                                                                 "data": date.today().isoformat(), "descricao": "Arroz e peixe"}))
    ok(client.delete(f"/api/alunos/{aluno['id']}/encarregados/{ee['id']}", headers=admin))


def test_pedagogico(client, admin):
    turma = ok(client.get("/api/turmas", headers=admin))[0]
    ano = next(a for a in ok(client.get("/api/anos-lectivos", headers=admin)) if a["estado"] == "activo")
    periodo = ok(client.get(f"/api/anos-lectivos/{ano['id']}/periodos", headers=admin))[0]
    lp = next(d for d in ok(client.get("/api/disciplinas", headers=admin)) if d["sigla"] == "LP")
    tipo_av = ok(client.post("/api/tipos-avaliacao", headers=admin, json={"designacao": "Trabalho de grupo", "peso": 1}))
    av = ok(client.post("/api/avaliacoes", headers=admin, json={"turma_id": turma["id"], "disciplina_id": lp["id"],
                                                               "periodo_id": periodo["id"], "tipo_id": tipo_av["id"],
                                                               "data": periodo["data_inicio"]}))
    notas = ok(client.get(f"/api/avaliacoes/{av['id']}/notas", headers=admin))
    ok(client.put(f"/api/avaliacoes/{av['id']}/notas", headers=admin,
                  json={"notas": [{"aluno_id": n["aluno_id"], "valor": 8} for n in notas]}))
    hoje = date.today().isoformat()
    ok(client.post("/api/assiduidade", headers=admin, json={"turma_id": turma["id"], "data": hoje,
                                                           "registos": [{"aluno_id": notas[0]["aluno_id"], "estado": "presente"}]}))
    tipo_oc = ok(client.post("/api/tipos-ocorrencia", headers=admin, json={"designacao": "Ajudou um colega", "gravidade": "positiva"}))
    oc = ok(client.post("/api/ocorrencias", headers=admin, json={"aluno_id": notas[0]["aluno_id"], "tipo_id": tipo_oc["id"],
                                                                "descricao": "Ajudou na aula"}))
    ok(client.patch(f"/api/ocorrencias/{oc['id']}", headers=admin, json={"estado": "fechada", "accao_tomada": "Elogio"}))
    ok(client.post("/api/registo-diario", headers=admin, json={"turma_id": turma["id"], "aluno_id": notas[0]["aluno_id"],
                                                              "comportamento": "bom"}))
    ok(client.get("/api/registo-diario", headers=admin, params={"turma_id": turma["id"]}))


def test_comunicacao_alertas_relatorios_primavera(client, admin):
    modelo = ok(client.post("/api/modelos-mensagem", headers=admin, json={"codigo": "aviso", "canal": "email",
                                                                         "assunto": "Aviso", "corpo": "Olá {aluno}"}))
    regra = ok(client.post("/api/regras", headers=admin, json={"nome": "Teste contrato", "evento": "apos_vencimento",
                                                              "dias_offset": 60, "canais": ["email"],
                                                              "modelo_id": modelo["id"], "activa": False}))
    ok(client.patch(f"/api/regras/{regra['id']}", headers=admin, json={"dias_offset": 90}))
    ok(client.post("/api/comunicados", headers=admin, json={"titulo": "Feriado", "corpo": "Não há aulas na sexta.",
                                                           "canais": ["app"]}))
    ok(client.post("/api/alertas/gerar", headers=admin))
    alerta = ok(client.get("/api/alertas", headers=admin))[0]
    ok(client.patch(f"/api/alertas/{alerta['id']}", headers=admin, json={"estado": "visto"}))
    rel = ok(client.post("/api/relatorios/agendados", headers=admin, json={
        "nome": "Turmas", "tipo": "cobranca_turma", "formato": "csv", "agenda_cron": "0 7 * * 1",
        "destinatarios": ["d@clacs.ao"]}))
    ok(client.patch(f"/api/relatorios/agendados/{rel['id']}", headers=admin, params={"activo": False}))
    ok(client.post(f"/api/relatorios/agendados/{rel['id']}/executar", headers=admin))
    assert ok(client.get(f"/api/relatorios/agendados/{rel['id']}/execucoes", headers=admin))[0]["estado"] == "ok"
    ok(client.put("/api/primavera/config", headers=admin, json={"versao": "V10"}))
    assert ok(client.post("/api/primavera/sincronizar", headers=admin))["detalhe"]["simulado"] is True


def test_importacao_respeita_capacidade(client, admin):
    cheia = next(t for t in ok(client.get("/api/turmas", headers=admin)) if t["cheia"])
    csv = f"nome_completo;turma\nSem Lugar;{cheia['designacao']}\n".encode("utf-8")
    lote = ok(client.post("/api/importacao/aluno", headers=admin, files={"ficheiro": ("a.csv", csv, "text/csv")}))
    assert lote["linhas_ok"] == 0
    erros = ok(client.get(f"/api/importacao/lotes/{lote['id']}/erros", headers=admin))
    assert "cheia" in erros[0]["mensagem"]


def test_contas(client, admin):
    ok(client.post("/api/utilizadores", headers=admin, json={"nome": "Conta Contrato", "email": "conta@teste.ao",
                                                            "senha": "senha-contrato-1", "perfis": ["secretaria"]}))
    uid = next(u["id"] for u in ok(client.get("/api/utilizadores", headers=admin)) if u["email"] == "conta@teste.ao")
    ok(client.patch(f"/api/utilizadores/{uid}", headers=admin, json={"perfis": ["secretaria", "financeiro"]}))
    h = login(client, "conta@teste.ao", "senha-contrato-1")
    ok(client.post("/api/auth/senha", headers=h, json={"senha_actual": "senha-contrato-1", "senha_nova": "senha-contrato-2"}))
    h = login(client, "conta@teste.ao", "senha-contrato-2")
    segredo = ok(client.post("/api/auth/2fa/iniciar", headers=h))["segredo"]
    ok(client.post("/api/auth/2fa/activar", headers=h, json={"codigo": pyotp.TOTP(segredo).now()}))
    ok(client.post("/api/auth/2fa/desactivar", headers=h, json={"codigo": pyotp.TOTP(segredo).now()}))


def test_portal(client, admin):
    ee = login(client, "encarregado@clacs.ao", get_settings().admin_password)
    aid = ok(client.get("/api/portal/eu", headers=ee))["educandos"][0]["id"]
    ano = next(a for a in ok(client.get("/api/anos-lectivos", headers=admin)) if a["estado"] == "activo")
    periodo = ok(client.get(f"/api/anos-lectivos/{ano['id']}/periodos", headers=admin))[0]
    assert ok(client.get(f"/api/portal/educandos/{aid}/boletim.pdf", headers=ee, params={"periodo_id": periodo["id"]})).content[:4] == b"%PDF"
    assert ok(client.get(f"/api/portal/educandos/{aid}/extracto.pdf", headers=ee)).content[:4] == b"%PDF"
    ok(client.get("/api/portal/pagamentos", headers=ee))
    ok(client.put("/api/portal/preferencias", headers=ee, json={"canal": "email", "activo": False}))
    ok(client.put("/api/portal/preferencias", headers=ee, json={"canal": "email", "activo": True}))  # repõe para os testes seguintes
    client.post(f"/api/alunos/{aid}/mensagem", headers=admin, json={"corpo": "Olá", "canais": ["app"]})
    mid = ok(client.get("/api/portal/mensagens", headers=ee))[0]["id"]
    ok(client.post(f"/api/portal/mensagens/{mid}/lida", headers=ee))
    ok(client.post("/api/portal/dispositivos", headers=ee, json={"token": "token-contrato-0001", "plataforma": "web"}))
    ok(client.delete("/api/portal/dispositivos", headers=ee, params={"token": "token-contrato-0001"}))


def test_erros_levam_cabecalhos_cors(client, admin):
    """Um erro (422 ou 500) chega ao browser com os cabeçalhos CORS e não como 'blocked by CORS policy'."""
    origem = {"Origin": "https://clacs-escola.vercel.app"}
    r = client.patch("/api/alertas/1", headers={**admin, **origem}, json={"estado": "resolvido"})
    assert r.status_code == 422 and r.headers.get("access-control-allow-origin")

    def falha():
        raise RuntimeError("erro de teste")

    app.add_api_route("/api/_teste_erro_cors", falha, include_in_schema=False)
    try:
        r = client.get("/api/_teste_erro_cors", headers=origem)
        assert r.status_code == 500 and "Referência" in r.json()["detail"]
        assert r.headers.get("access-control-allow-origin")
    finally:
        app.router.routes[:] = [rt for rt in app.router.routes if getattr(rt, "path", "") != "/api/_teste_erro_cors"]
