"""Terceira fase: plano de pagamento, desconto de irmãos, refeição avulsa, indicadores, assistente com IA, push, Z-API.

Corre antes de test_completo.py (ordem alfabética), cujo último teste encerra o ano lectivo activo.
"""
from datetime import date
from types import SimpleNamespace

import httpx
import pytest

from app.config import get_settings
from app.database import SessionLocal
from app.integracoes import mensagens
from app.models import Cobranca
from app.services import assistente_ia
from tests.conftest import login


def _ano(client, admin):
    return next(a for a in client.get("/api/anos-lectivos", headers=admin).json() if a["estado"] == "activo")


def _aluno(client, admin, nome):
    return client.get("/api/alunos", headers=admin, params={"q": nome}).json()[0]


# ---------------------------------------------------------------- plano de pagamento
def test_nao_gera_fora_do_plano(client, admin):
    ano = _ano(client, admin)
    julho = f"{ano['data_fim'][:4]}-07-01"  # o plano de demonstração vai de Setembro a Junho
    r = client.post("/api/cobrancas/gerar", headers=admin, json={"competencia": julho}).json()
    assert r["fora_do_plano"] and r["criadas"] == 0 and "plano" in r["motivo"]
    r = client.post("/api/cobrancas/gerar", headers=admin, json={"competencia": "2040-01-01"}).json()
    assert r["fora_do_plano"] and "ano lectivo" in r["motivo"]


def test_gerar_exige_propina_preco_e_multa(client, admin):
    """O mapa de cobrança só é gerado com a propina, o preço de cada classe e a percentagem da multa."""
    ano = _ano(client, admin)
    novembro = f"{ano['data_inicio'][:4]}-11-01"
    cfg = client.get("/api/cobrancas/configuracao", headers=admin).json()
    assert cfg["pronto"] and cfg["pendentes"] == []
    propina = cfg["propinas"][0]
    assert propina["multa"]["tipo"] == "percentual" and all(p["valor"] for p in propina["precos"])

    # uma segunda propina sem preço nem multa bloqueia o "Gerar" e diz o que falta
    servico = {"codigo": "PROP-B", "nome": "Propina B", "categoria": "propina", "periodicidade": "mensal"}
    sid = client.post("/api/servicos", headers=admin, json=servico).json()["id"]
    try:
        r = client.post("/api/cobrancas/gerar", headers=admin, json={"competencia": novembro})
        assert r.status_code == 422 and "preço da Propina B" in r.json()["detail"]
        assert "percentagem da multa da Propina B" in r.json()["detail"]
        r = client.post("/api/cobrancas/gerar", headers=admin, json={"competencia": novembro, "valor": 30000})
        assert r.status_code == 422 and "servico_id" in r.json()["detail"]  # duas propinas: qual configurar?

        # a configuração vem no próprio "Gerar": preço para todas as classes + multa → regra criada sozinha
        r = client.post("/api/cobrancas/gerar", headers=admin, json={
            "competencia": novembro, "servico_id": sid, "valor": 30000, "multa_percentual": 5, "dias_tolerancia": 3})
        assert r.status_code == 200, r.text
        assert r.json()["criadas"] > 0 and isinstance(r.json()["multas_aplicadas"], int)
        b = next(p for p in client.get("/api/cobrancas/configuracao", headers=admin).json()["propinas"]
                 if p["servico_id"] == sid)
        assert {p["valor"] for p in b["precos"]} == {30000}
        assert b["multa"] == {"tipo": "percentual", "valor": 5, "dias_tolerancia": 3, "maximo": None}
        regra = next(m for m in client.get("/api/multas", headers=admin).json() if m["servico_id"] == sid)
        assert regra["tipo"] == "percentual" and float(regra["valor"]) == 5
    finally:
        client.patch(f"/api/servicos/{sid}", headers=admin, json={**servico, "activo": False})
        with SessionLocal() as db:  # retira as cobranças de teste da Propina B para não mexer nos outros testes
            db.query(Cobranca).filter(Cobranca.servico_id == sid).delete()
            db.commit()

    # mudar preços e multas pelo "Gerar" exige propinas/editar; só gerar, não
    secretaria = login(client, "secretaria@teste.ao", "secretaria-123")
    r = client.post("/api/cobrancas/gerar", headers=secretaria, json={"competencia": novembro, "multa_percentual": 50})
    assert r.status_code == 403
    assert client.post("/api/cobrancas/gerar", headers=secretaria, json={"competencia": novembro}).status_code == 200


def test_desconto_de_irmaos_automatico(client, admin):
    ana, bento = _aluno(client, admin, "Ana Cota"), _aluno(client, admin, "Bento Lima")
    ee_ana = client.get(f"/api/alunos/{ana['id']}", headers=admin).json()["encarregados"][0]["encarregado_id"]
    r = client.post(f"/api/alunos/{bento['id']}/encarregados", headers=admin,
                    json={"encarregado_id": ee_ana, "parentesco": "mae", "principal": True, "responsavel_financeiro": True})
    assert r.status_code == 201
    ano = _ano(client, admin)
    dezembro = f"{ano['data_inicio'][:4]}-12-01"
    r = client.post("/api/cobrancas/gerar", headers=admin, json={"competencia": dezembro}).json()
    assert r["criadas"] > 0 and r["com_desconto_irmaos"] >= 1

    def propina(aid):
        linhas = client.get("/api/cobrancas", headers=admin, params={"aluno_id": aid, "competencia": dezembro}).json()["linhas"]
        return next(x for x in linhas if x["categoria"] == "propina")
    assert propina(ana["id"])["valor_total"] == 45000           # o primeiro educando paga o valor inteiro
    assert propina(bento["id"])["valor_total"] == 45000 * 0.9   # os seguintes têm 10% de desconto


# ---------------------------------------------------------------- refeitório
def test_refeicao_avulsa_gera_uma_conta_mensal(client, admin):
    carla = _aluno(client, admin, "Carla Neto")  # sem adesão ao refeitório nos dados de demonstração
    tipos = client.get("/api/refeitorio/tipos", headers=admin).json()
    hoje = date.today().isoformat()
    for t in tipos:
        r = client.post("/api/refeitorio/registos", headers=admin,
                        json={"refeicao_tipo_id": t["id"], "data": hoje, "aluno_ids": [carla["id"]], "valor_avulso": 1500})
        assert r.json()["refeicoes_cobradas"] == 1
    linhas = client.get("/api/cobrancas", headers=admin, params={"aluno_id": carla["id"]}).json()["linhas"]
    avulsas = [x for x in linhas if x["servico"] == "Refeições avulsas"]
    assert len(avulsas) == 1 and avulsas[0]["valor_total"] == 1500 * len(tipos)


# ---------------------------------------------------------------- indicadores
def test_indicadores_configuraveis(client, admin):
    base = client.get("/api/indicadores", headers=admin).json()
    assert [i["codigo"] for i in base] == ["recebido_mes", "em_atraso", "alunos_incumprimento", "taxa_cobranca"]
    assert client.post("/api/indicadores", headers=admin, json={"codigo": "nao_existe"}).status_code == 422
    novo = client.post("/api/indicadores", headers=admin, json={"codigo": "ocorrencias_abertas", "perfil_minimo": "coordenacao"})
    assert novo.status_code == 201
    painel = client.get("/api/painel", headers=admin).json()
    assert "ocorrencias_abertas" in [i["codigo"] for i in painel["indicadores"]]
    assert client.delete(f"/api/indicadores/{novo.json()['id']}", headers=admin).status_code == 204
    prof = login(client, "professor@clacs.ao", get_settings().admin_password)
    assert client.get("/api/indicadores", headers=prof).status_code == 403


# ---------------------------------------------------------------- assistente
class ClienteFalso:
    """Imita client.beta.messages.create: 1.º pede a ferramenta contar_alunos, 2.º responde com o resultado."""

    def __init__(self):
        self.pedidos = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.pedidos.append(kw)
        if len(self.pedidos) == 1:
            uso = SimpleNamespace(type="tool_use", id="t1", name="contar_alunos", input={"turma": "7.ª A"})
            return SimpleNamespace(stop_reason="tool_use", content=[uso])
        resultado = kw["messages"][-1]["content"][0]["content"]
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=f"Dados: {resultado}")])


def test_assistente_ia_usa_ferramentas_do_estabelecimento():
    falso = ClienteFalso()
    with SessionLocal() as db:
        resposta, chamadas = assistente_ia.responder(db, 1, "Quantos alunos tem a 7.ª A?", cliente=falso)
    assert chamadas == [{"ferramenta": "contar_alunos", "entrada": {"turma": "7.ª A"}}]
    assert '"alunos": 4' in resposta
    pedido = falso.pedidos[0]
    assert pedido["model"] == "claude-opus-5-5" and pedido["fallbacks"] == "default"
    assert all(f["strict"] for f in pedido["tools"])


def test_assistente_sem_chave_responde_por_regras(client, admin):
    r = client.post("/api/assistente", headers=admin, json={"pergunta": "Qual a turma com mais atraso?"}).json()
    assert r["fonte"] == "regras" and r["resposta"].startswith("A turma")


# ---------------------------------------------------------------- push e Z-API
def test_mensagem_app_chega_ao_dispositivo(client, admin):
    ee = login(client, "encarregado@clacs.ao", get_settings().admin_password)
    assert client.post("/api/portal/dispositivos", headers=ee,
                       json={"token": "token-de-teste-123456", "plataforma": "android"}).status_code == 201
    ana = _aluno(client, admin, "Ana Cota")
    client.post(f"/api/alunos/{ana['id']}/mensagem", headers=admin, json={"corpo": "Reunião de pais", "canais": ["app"]})
    client.post("/api/mensagens/despachar", headers=admin)
    msg = client.get("/api/mensagens", headers=admin, params={"aluno_id": ana["id"]}).json()[0]
    assert msg["estado"] == "entregue" and msg["id_externo"] == "push:1"
    assert any(m["corpo"] == "Reunião de pais" for m in client.get("/api/portal/mensagens", headers=ee).json())


def test_whatsapp_por_zapi(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "whatsapp_provedor", "zapi")
    monkeypatch.setattr(s, "zapi_instance_id", "INST")
    monkeypatch.setattr(s, "zapi_token", "TOK")
    monkeypatch.setattr(s, "zapi_client_token", "SEG")
    enviado = {}

    def post(url, json=None, headers=None, timeout=None):
        enviado.update(url=url, json=json, headers=headers)
        return httpx.Response(200, json={"zaapId": "z1", "messageId": "m1"})

    monkeypatch.setattr(mensagens.httpx, "post", post)
    assert mensagens.enviar_whatsapp("+244 923 000 111", "Olá") == "m1"
    assert enviado["url"] == "https://api.z-api.io/instances/INST/token/TOK/send-text"
    assert enviado["json"] == {"phone": "244923000111", "message": "Olá"}
    assert enviado["headers"] == {"Client-Token": "SEG"}


def test_whatsapp_por_evolution(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "whatsapp_provedor", "evolution")
    monkeypatch.setattr(s, "evolution_url", "https://whatsapp-api.exemplo.com/")
    monkeypatch.setattr(s, "evolution_instancia", "hunter-abc")
    monkeypatch.setattr(s, "evolution_apikey", "CHAVE")
    enviado = {}

    def post(url, json=None, headers=None, timeout=None):
        enviado.update(url=url, json=json, headers=headers)
        return httpx.Response(201, json={"key": {"remoteJid": "244923000111@s.whatsapp.net", "id": "3EB0ABC"},
                                         "status": "PENDING"})

    monkeypatch.setattr(mensagens.httpx, "post", post)
    assert mensagens.enviar_whatsapp("+244 923 000 111", "Olá") == "3EB0ABC"
    assert enviado["url"] == "https://whatsapp-api.exemplo.com/message/sendText/hunter-abc"
    assert enviado["json"] == {"number": "244923000111", "text": "Olá"}
    assert enviado["headers"] == {"apikey": "CHAVE"}

    monkeypatch.setattr(mensagens.httpx, "post", lambda *a, **k: httpx.Response(404, text="instância não existe"))
    with pytest.raises(mensagens.ErroIntegracao):
        mensagens.enviar_whatsapp("923000111", "Olá")


def test_whatsapp_zapi_erro(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "whatsapp_provedor", "zapi")
    monkeypatch.setattr(s, "zapi_instance_id", "INST")
    monkeypatch.setattr(s, "zapi_token", "TOK")
    monkeypatch.setattr(mensagens.httpx, "post", lambda *a, **k: httpx.Response(401, text="instância desligada"))
    with pytest.raises(mensagens.ErroIntegracao):
        mensagens.enviar_whatsapp("923000111", "Olá")


# ---------------------------------------------------------------- mapa de cobrança do ano inteiro
# (no fim: gera todos os meses do ano, que os testes acima geram um a um)
def test_gerar_o_ano_inteiro_com_plano_e_alertas(client, admin):
    """Ponto 3: 10 prestações a partir de Setembro, vencimento no dia 10, lembretes 20/15/5/0 dias antes."""
    ano = _ano(client, admin)
    setembro = f"{ano['data_inicio'][:4]}-09-01"
    r = client.post("/api/cobrancas/gerar", headers=admin, json={
        "competencia": setembro, "prestacoes": 10, "mes_inicio": 9, "dia_vencimento": 10, "todo_o_ano": True})
    assert r.status_code == 200, r.text
    meses = [m["competencia"][5:7] for m in r.json()["meses"]]
    assert meses == ["09", "10", "11", "12", "01", "02", "03", "04", "05", "06"]  # Julho e Agosto ficam de fora
    cfg = client.get("/api/cobrancas/configuracao", headers=admin).json()
    assert cfg["plano"] == {"prestacoes": 10, "mes_inicio": 9, "dia_vencimento": 10,
                            "meses": [9, 10, 11, 12, 1, 2, 3, 4, 5, 6]}
    assert cfg["alertas_dias"] == [20, 15, 5, 0]  # criados por omissão no primeiro "Gerar"
    regras = [x for x in client.get("/api/regras", headers=admin).json() if x["evento"] == "antes_vencimento"]
    assert {x["dias_offset"] for x in regras if x["activa"]} >= {20, 15, 5, 0}

    # repetir não duplica e os alertas podem mudar: só 5 dias antes e no próprio dia
    r = client.post("/api/cobrancas/gerar", headers=admin, json={"competencia": setembro, "todo_o_ano": True,
                                                                  "alertas_dias": [0, 5]}).json()
    assert r["criadas"] == 0
    assert client.get("/api/cobrancas/configuracao", headers=admin).json()["alertas_dias"] == [5, 0]
    r = client.post("/api/cobrancas/gerar", headers=admin, json={"competencia": setembro, "alertas_dias": [99]})
    assert r.status_code == 422
    client.post("/api/cobrancas/gerar", headers=admin, json={"competencia": setembro, "alertas_dias": [20, 15, 5, 0]})
