from datetime import date

from sqlalchemy import select

from app.database import SessionLocal
from app.models import Estabelecimento, Perfil, Utilizador, UtilizadorPerfil
from app.security import hash_senha
from tests.conftest import login


def _dividas(client, h, **filtros):
    return client.get("/api/cobrancas", params={"estado": "divida", **filtros}, headers=h).json()["linhas"]


# ---------------------------------------------------------------- autenticação e permissões
def test_login_errado_e_sem_token(client):
    assert client.post("/api/auth/login", data={"username": "admin@clacs.ao", "password": "errada"}).status_code == 401
    assert client.get("/api/painel").status_code == 401
    assert client.get("/api/painel", headers={"Authorization": "Bearer lixo"}).status_code == 401


def test_me(client, admin):
    me = client.get("/api/auth/me", headers=admin).json()
    assert "admin" in me["perfis"] and me["estabelecimento"]["nome"] == "Colégio Cota Lionel"


def test_perfil_secretaria_tem_acesso_limitado(client, admin):
    r = client.post("/api/utilizadores", headers=admin, json={
        "nome": "Secretaria", "email": "secretaria@teste.ao", "senha": "secretaria-123", "perfis": ["secretaria"]})
    assert r.status_code == 201, r.text
    h = login(client, "secretaria@teste.ao", "secretaria-123")
    assert client.get("/api/primavera", headers=h).status_code == 403
    assert client.get("/api/utilizadores", headers=h).status_code == 403
    assert client.get("/api/alunos", headers=h).status_code == 200
    assert client.post("/api/cobrancas/aplicar-multas", headers=h).status_code == 403


def test_isolamento_entre_estabelecimentos(client, admin):
    with SessionLocal() as db:
        outro = Estabelecimento(nome="Outra Escola")
        db.add(outro)
        db.flush()
        u = Utilizador(nome="Admin 2", email="admin2@outra.ao", senha_hash=hash_senha("outra-escola-1"))
        db.add(u)
        db.flush()
        pid = db.scalar(select(Perfil.id).where(Perfil.codigo == "admin"))
        db.add(UtilizadorPerfil(utilizador_id=u.id, perfil_id=pid, estabelecimento_id=outro.id))
        db.commit()
    h = login(client, "admin2@outra.ao", "outra-escola-1")
    assert client.get("/api/alunos", headers=h).json() == []
    assert client.get("/api/alunos/1/360", headers=h).status_code == 404
    assert client.get("/api/cobrancas", headers=h).json()["linhas"] == []


# ---------------------------------------------------------------- cadastro
def test_criar_aluno_com_encarregado_e_matricula(client, admin):
    turma = client.get("/api/turmas", headers=admin).json()[0]
    ee = client.post("/api/encarregados", headers=admin, json={"nome_completo": "Joana Nova", "telefone": "+244 923 000 111",
                                                               "nif": "5499999999"}).json()
    r = client.post("/api/alunos", headers=admin, json={"nome_completo": "Aluno Novo", "turma_id": turma["id"],
                                                        "encarregado_id": ee["id"], "parentesco": "mae"})
    assert r.status_code == 201, r.text
    a = r.json()
    v = client.get(f"/api/alunos/{a['id']}/360", headers=admin).json()
    assert v["turma"]["id"] == turma["id"]
    assert v["encarregados"][0]["nome"] == "Joana Nova" and v["encarregados"][0]["responsavel_financeiro"]
    assert any(e["id"] == ee["id"] and e["educandos"] for e in client.get("/api/encarregados", headers=admin).json())


def test_turma_cheia(client, admin):
    classe = client.get("/api/classes", headers=admin).json()[0]
    t = client.post("/api/turmas", headers=admin, json={"designacao": "9.ª Z", "classe_id": classe["id"], "capacidade": 1}).json()
    assert client.post("/api/alunos", headers=admin, json={"nome_completo": "Primeiro", "turma_id": t["id"]}).status_code == 201
    r = client.post("/api/alunos", headers=admin, json={"nome_completo": "Segundo", "turma_id": t["id"]})
    assert r.status_code == 409 and "cheia" in r.json()["detail"]


def test_mudanca_de_turma_fica_no_historico(client, admin):
    turmas = client.get("/api/turmas", headers=admin).json()
    aluno = client.get("/api/alunos", headers=admin, params={"q": "Bento"}).json()[0]
    destino = next(t for t in turmas if t["id"] != aluno["turma_id"] and not t["cheia"])
    r = client.post("/api/matriculas", headers=admin, json={"aluno_id": aluno["id"], "turma_id": destino["id"]}).json()
    assert r["mudanca_de_turma"] is True
    hist = client.get(f"/api/matriculas/{r['id']}/historico", headers=admin).json()
    assert hist[-1]["turma_anterior_id"] == aluno["turma_id"] and hist[-1]["turma_nova_id"] == destino["id"]


# ---------------------------------------------------------------- financeiro
def test_gerar_cobrancas_e_idempotente(client, admin):
    comp = date.today().replace(day=1)
    proximo = date(comp.year + comp.month // 12, comp.month % 12 + 1, 1).isoformat()
    r1 = client.post("/api/cobrancas/gerar", headers=admin, json={"competencia": proximo}).json()
    assert r1["criadas"] > 20 and r1["servicos_sem_preco"] == []
    r2 = client.post("/api/cobrancas/gerar", headers=admin, json={"competencia": proximo}).json()
    assert r2["criadas"] == 0


def test_pagamento_parcial_total_e_excesso(client, admin):
    c = _dividas(client, admin, categoria="propina")[0]
    metodo = client.get("/api/metodos-pagamento", headers=admin).json()[0]["id"]
    total = c["valor_em_aberto"]
    r = client.post("/api/pagamentos", headers=admin, json={"metodo_id": metodo, "valor": 10000, "cobranca_ids": [c["cobranca_id"]]})
    assert r.status_code == 201, r.text
    linha = next(x for x in client.get("/api/cobrancas", params={"aluno_id": c["aluno_id"]}, headers=admin).json()["linhas"]
                 if x["cobranca_id"] == c["cobranca_id"])
    assert linha["estado"] == "parcial" and linha["valor_em_aberto"] == total - 10000
    r = client.post("/api/pagamentos", headers=admin, json={"metodo_id": metodo, "valor": total, "cobranca_ids": [c["cobranca_id"]]})
    assert r.status_code == 422  # excede o que falta
    r = client.post("/api/pagamentos", headers=admin, json={"metodo_id": metodo, "valor": total - 10000, "cobranca_ids": [c["cobranca_id"]]})
    assert r.status_code == 201
    linha = next(x for x in client.get("/api/cobrancas", params={"aluno_id": c["aluno_id"]}, headers=admin).json()["linhas"]
                 if x["cobranca_id"] == c["cobranca_id"])
    assert linha["estado"] == "paga" and linha["valor_em_aberto"] == 0


def test_pagamento_do_encarregado_salda_as_mais_antigas(client, admin):
    ee = _dividas(client, admin, categoria="propina")[0]["encarregado_id"]
    minhas = sorted(_dividas(client, admin, encarregado_id=ee), key=lambda x: (x["vencimento"], x["cobranca_id"]))
    metodo = client.get("/api/metodos-pagamento", headers=admin).json()[0]["id"]
    r = client.post("/api/pagamentos", headers=admin, json={"metodo_id": metodo, "valor": minhas[0]["valor_em_aberto"],
                                                           "encarregado_id": ee}).json()
    assert r["alocacoes"] == [{"cobranca_id": minhas[0]["cobranca_id"], "valor": minhas[0]["valor_em_aberto"]}]


def test_rejeitar_pagamento_reabre_a_cobranca(client, admin):
    c = _dividas(client, admin)[0]
    metodo = client.get("/api/metodos-pagamento", headers=admin).json()[0]["id"]
    pg = client.post("/api/pagamentos", headers=admin, json={"metodo_id": metodo, "valor": c["valor_em_aberto"],
                                                            "cobranca_ids": [c["cobranca_id"]]}).json()
    assert client.post(f"/api/pagamentos/{pg['id']}/rejeitar", headers=admin).status_code == 200
    linha = next(x for x in _dividas(client, admin) if x["cobranca_id"] == c["cobranca_id"])
    assert linha["estado"] == "pendente"


def test_exportar_excel(client, admin):
    r = client.get("/api/cobrancas/exportar", headers=admin, params={"estado": "divida"})
    assert r.status_code == 200 and r.content[:2] == b"PK"
    assert "spreadsheetml" in r.headers["content-type"]


# ---------------------------------------------------------------- painel, comunicação, assistente
def test_painel_e_alertas(client, admin):
    p = client.get("/api/painel", headers=admin).json()
    assert p["kpis"]["alunos"] >= 20 and len(p["por_turma"]) >= 5
    tipos = {a["tipo"] for a in p["alertas"]}
    assert {"risco_incumprimento", "queda_desempenho", "divergencia_primavera"} <= tipos
    assert client.post("/api/alertas/gerar", headers=admin).json()["novos"] > 0
    assert client.post("/api/alertas/gerar", headers=admin).json()["novos"] == 0


def test_lembretes_e_regras_nao_repetem(client, admin):
    r = client.post("/api/cobrancas/lembretes", headers=admin, json={"canais": ["whatsapp"]}).json()
    assert r["mensagens"] > 0
    primeira = client.post("/api/regras/executar", headers=admin).json()
    segunda = client.post("/api/regras/executar", headers=admin).json()
    assert sum(primeira.values()) > 0 and sum(segunda.values()) == 0
    d = client.post("/api/mensagens/despachar", headers=admin).json()
    assert d["enviadas"] > 0


def test_assistente(client, admin):
    r = client.post("/api/assistente", headers=admin, json={"pergunta": "Quantos alunos da 7.ª estão em dívida?"}).json()
    assert "7.ª A" in r["resposta"] and "dívida" in r["resposta"]
    r = client.post("/api/assistente", headers=admin, json={"pergunta": "Qual a turma com mais atraso?"}).json()
    assert r["resposta"].startswith("A turma")


def test_primavera_resolver_cobranca_paga(client, admin):
    div = next(d for d in client.get("/api/primavera/divergencias", headers=admin).json() if d["entidade"] == "cobranca")
    r = client.post(f"/api/primavera/divergencias/{div['id']}/resolver", headers=admin)
    assert r.status_code == 200 and r.json()["estado"] == "resolvida"
    todas = client.get("/api/cobrancas", headers=admin).json()["linhas"]
    assert next(x for x in todas if x["cobranca_id"] == div["id_local"])["estado"] == "paga"
    assert client.post(f"/api/primavera/divergencias/{div['id']}/resolver", headers=admin).status_code == 409


def test_refeitorio(client, admin):
    r = client.get("/api/refeitorio", headers=admin).json()
    assert r["totais"]["alimentacao"] == 7 and r["totais"]["so_utilizacao"] == 7
    tipo = client.get("/api/refeitorio/tipos", headers=admin).json()[1]["id"]
    ids = [l["aluno_id"] for l in r["linhas"][:3]]
    assert client.post("/api/refeitorio/registos", headers=admin, json={"refeicao_tipo_id": tipo, "aluno_ids": ids}).json()["registados"] == 3


def test_pedagogico_pauta(client, admin):
    turma = client.get("/api/turmas", headers=admin).json()[0]
    ano = next(a for a in client.get("/api/anos-lectivos", headers=admin).json() if a["estado"] == "activo")
    periodo = client.get(f"/api/anos-lectivos/{ano['id']}/periodos", headers=admin).json()[0]
    pauta = client.get("/api/pautas", headers=admin, params={"turma_id": turma["id"], "periodo_id": periodo["id"]}).json()
    # Alunos que entraram na turma depois das avaliações (outros testes) ainda não têm notas.
    assert len(pauta["disciplinas"]) == 3 and sum(l["media_geral"] is not None for l in pauta["linhas"]) >= 3
    r = client.post("/api/pautas/fechar", headers=admin, params={"turma_id": turma["id"], "periodo_id": periodo["id"]})
    assert r.json()["resultados"] > 0


def test_importar_alunos_csv(client, admin):
    turma = next(t for t in client.get("/api/turmas", headers=admin).json() if not t["cheia"])["designacao"]
    csv = ("nome_completo;data_nascimento;sexo;turma;encarregado_nif;parentesco\n"
           f"Importado Um;12/03/2015;F;{turma};5400000000;Mãe\n"
           "Importado Dois;2015-05-01;M;Turma Inexistente;;\n").encode("utf-8")
    r = client.post("/api/importacao/aluno", headers=admin, files={"ficheiro": ("alunos.csv", csv, "text/csv")})
    assert r.status_code == 201, r.text
    lote = r.json()
    assert lote["linhas_ok"] == 1 and lote["erros"] == 1
    erros = client.get(f"/api/importacao/lotes/{lote['id']}/erros", headers=admin).json()
    assert erros[0]["linha"] == 3 and "não existe" in erros[0]["mensagem"]
    assert client.get("/api/alunos", headers=admin, params={"q": "Importado Dois"}).json() == []
