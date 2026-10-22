"""Fila de mensagens, modelos e motor de regras de automação."""
import logging
import re
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integracoes import ErroIntegracao
from app.integracoes.email import enviar_email
from app.integracoes.mensagens import enviar_sms, enviar_whatsapp
from app.integracoes.push import enviar_ao_encarregado
from app.models import (
    Alerta, Aluno, Encarregado, Mensagem, ModeloMensagem, Ocorrencia, PreferenciaContacto, RegraAutomacao,
    TipoOcorrencia,
)
from app.services.financeiro import cobrancas_detalhe, encarregado_financeiro

log = logging.getLogger("clacs.mensagens")

MODELOS_PADRAO = {
    "antes_vencimento": "Caro encarregado, a {servico} de {aluno} vence a {data}. Valor: {valor}. "
                        "Pode pagar na escola ou no portal do encarregado.",
    "apos_vencimento": "Caro encarregado, a {servico} de {aluno} venceu a {data} e continua por pagar. Valor: {valor}. "
                       "Pode pagar na escola ou no portal do encarregado.",
    "lembrete": "Caro encarregado, a {servico} de {aluno} ({competencia}) está em dívida há {dias} dia(s). Valor: {valor}.",
    "ocorrencia": "Caro encarregado, foi registada uma ocorrência sobre {aluno}: {descricao}.",
}


def kz(v: float) -> str:
    return f"{v:,.0f}".replace(",", " ") + " Kz"


def render(corpo: str, variaveis: dict) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: str(variaveis.get(m.group(1), m.group(0))), corpo)


def variaveis_cobranca(x: dict) -> dict:
    return {"aluno": x["aluno"], "encarregado": x["encarregado"], "servico": x["servico"].lower(),
            "valor": kz(x["valor_em_aberto"]), "data": x["vencimento"].strftime("%d/%m/%Y"),
            "competencia": x["competencia"].strftime("%m/%Y"), "dias": x["dias_atraso"], "turma": x["turma"]}


def _corpo(db: Session, est: int, codigo: str | None, modelo_id: int | None, canal: str) -> tuple[str | None, str]:
    m = None
    if modelo_id:
        m = db.get(ModeloMensagem, modelo_id)
    elif codigo:
        m = db.scalar(select(ModeloMensagem).where(ModeloMensagem.estabelecimento_id == est,
                                                   ModeloMensagem.codigo == codigo, ModeloMensagem.canal == canal))
    if m:
        return m.assunto, m.corpo
    return None, MODELOS_PADRAO.get(codigo or "", MODELOS_PADRAO["lembrete"])


def enfileirar_lembretes(db: Session, est: int, linhas: list[dict], canais: list[str],
                         codigo: str = "lembrete", regra_id: int | None = None,
                         modelo_id: int | None = None) -> int:
    """Cria uma mensagem por cobrança e canal. Com regra_id, não repete o mesmo envio."""
    ja = set()
    if regra_id:
        ja = set(db.execute(select(Mensagem.cobranca_id, Mensagem.canal).where(Mensagem.regra_id == regra_id)).tuples())
    n = 0
    for x in linhas:
        if not x["encarregado_id"]:
            continue
        for canal in canais:
            if (x["cobranca_id"], canal) in ja:
                continue
            assunto, corpo = _corpo(db, est, codigo, modelo_id, canal)
            v = variaveis_cobranca(x)
            db.add(Mensagem(estabelecimento_id=est, regra_id=regra_id, encarregado_id=x["encarregado_id"],
                            aluno_id=x["aluno_id"], cobranca_id=x["cobranca_id"], canal=canal,
                            assunto=render(assunto, v) if assunto else None, corpo=render(corpo, v)))
            n += 1
    db.commit()
    return n


def executar_regras(db: Session, est: int, hoje: date | None = None) -> dict:
    """Avalia as regras activas. Pensado para correr uma vez por dia (cron do Render)."""
    hoje = hoje or date.today()
    resultado = {}
    abertas = cobrancas_detalhe(db, est, estado="divida", hoje=hoje)
    for r in db.scalars(select(RegraAutomacao).where(RegraAutomacao.estabelecimento_id == est,
                                                     RegraAutomacao.activa)):
        d = r.dias_offset or 0
        cond = r.condicao or {}
        alvo = [x for x in abertas
                if (not cond.get("turma_id") or x["turma_id"] == cond["turma_id"])
                and (not cond.get("categoria") or x["categoria"] == cond["categoria"])
                and x["valor_em_aberto"] >= cond.get("valor_minimo", 0)]
        n = 0
        if r.evento == "antes_vencimento":
            alvo = [x for x in alvo if hoje <= x["vencimento"] <= hoje + timedelta(days=d)]
        elif r.evento == "apos_vencimento":
            alvo = [x for x in alvo if x["vencimento"] <= hoje - timedelta(days=d)]
        elif r.evento == "divida_acima_dias":
            alvo = [x for x in alvo if x["dias_atraso"] > d]
        elif r.evento == "ocorrencia":
            n = _regra_ocorrencias(db, est, r)
            resultado[r.nome] = n
            continue
        else:
            # nota_baixa e faltas_excessivas são tratadas pelos alertas pedagógicos.
            continue

        if r.destino == "direccao":
            n = _alerta_direccao(db, est, r, alvo)
        else:
            codigo = "apos_vencimento" if r.evento != "antes_vencimento" else "antes_vencimento"
            n = enfileirar_lembretes(db, est, alvo, list(r.canais), codigo=codigo, regra_id=r.id, modelo_id=r.modelo_id)
        resultado[r.nome] = n
    db.commit()
    return resultado


def _alerta_direccao(db: Session, est: int, r: RegraAutomacao, alvo: list[dict]) -> int:
    existentes = set(db.scalars(select(Alerta.aluno_id).where(
        Alerta.estabelecimento_id == est, Alerta.tipo == "risco_incumprimento", Alerta.estado.in_(("novo", "visto")))))
    n = 0
    for aid in {x["aluno_id"] for x in alvo} - existentes:
        linhas = [x for x in alvo if x["aluno_id"] == aid]
        total = sum(x["valor_em_aberto"] for x in linhas)
        db.add(Alerta(estabelecimento_id=est, tipo="risco_incumprimento", aluno_id=aid, gravidade="alta",
                      mensagem=f"{linhas[0]['aluno']} ({linhas[0]['turma']}): dívida de {kz(total)} há mais de "
                               f"{r.dias_offset} dias.",
                      dados={"regra_id": r.id, "total": total, "maior_atraso": max(x["dias_atraso"] for x in linhas)}))
        n += 1
    return n


def _regra_ocorrencias(db: Session, est: int, r: RegraAutomacao) -> int:
    ja = set(db.scalars(select(Mensagem.aluno_id).where(Mensagem.regra_id == r.id)))
    n = 0
    for o, nome in db.execute(select(Ocorrencia, Aluno.nome_completo).join(Aluno, Aluno.id == Ocorrencia.aluno_id)
                              .join(TipoOcorrencia, TipoOcorrencia.id == Ocorrencia.tipo_id)
                              .where(TipoOcorrencia.estabelecimento_id == est, Ocorrencia.notificar_ee,
                                     Ocorrencia.estado == "aberta")):
        ee = encarregado_financeiro(db, o.aluno_id)
        if not ee or o.aluno_id in ja:
            continue
        for canal in r.canais:
            _, corpo = _corpo(db, est, "ocorrencia", r.modelo_id, canal)
            db.add(Mensagem(estabelecimento_id=est, regra_id=r.id, encarregado_id=ee, aluno_id=o.aluno_id,
                            canal=canal, corpo=render(corpo, {"aluno": nome, "descricao": o.descricao})))
            n += 1
    return n


# ---------------------------------------------------------------- envio
def enviar(canal: str, destino: str, assunto: str | None, corpo: str) -> str:
    """Entrega uma mensagem pelo canal; cada adaptador fica em modo simulado até ter credenciais no .env."""
    if canal == "whatsapp":
        return enviar_whatsapp(destino, corpo)
    if canal == "sms":
        return enviar_sms(destino, corpo)
    if canal == "email":
        return enviar_email(destino, assunto or "CLACS Escola", corpo)
    # "app": fica disponível no portal do encarregado (GET /api/portal/mensagens).
    return f"app-{destino}"


def despachar(db: Session, est: int, limite: int = 200) -> dict:
    agora = datetime.now(timezone.utc)
    enviadas = falhadas = 0
    fila = db.execute(select(Mensagem, Encarregado).join(Encarregado, Encarregado.id == Mensagem.encarregado_id)
                      .where(Mensagem.estabelecimento_id == est, Mensagem.estado == "agendada")
                      .order_by(Mensagem.agendada_para).limit(limite)).all()
    recusas = set(db.execute(select(PreferenciaContacto.encarregado_id, PreferenciaContacto.canal)
                             .where(PreferenciaContacto.activo.is_(False))).tuples())
    for m, ee in fila:
        destino = {"whatsapp": ee.whatsapp or ee.telefone, "sms": ee.telefone, "email": ee.email,
                   "app": str(ee.id)}.get(m.canal)
        if (ee.id, m.canal) in recusas:
            m.estado, m.erro = "falhada", f"O encarregado desactivou o canal {m.canal}"
            falhadas += 1
            continue
        if not destino:
            m.estado, m.erro = "falhada", f"Encarregado sem contacto para {m.canal}"
            falhadas += 1
            continue
        try:
            if m.canal == "app":
                # Fica no portal; se o encarregado tiver a app instalada, recebe também uma notificação push.
                n = enviar_ao_encarregado(db, ee.id, m.assunto or "CLACS Escola", m.corpo)
                m.id_externo = f"push:{n}"
                m.estado, m.enviada_em = "entregue", agora
                enviadas += 1
                continue
            m.id_externo = enviar(m.canal, destino, m.assunto, m.corpo)
            m.estado, m.enviada_em = ("entregue" if m.canal == "app" else "enviada"), agora
            enviadas += 1
        except ErroIntegracao as e:  # o fornecedor pode falhar; a mensagem fica registada com o motivo
            m.estado, m.erro = "falhada", str(e)[:500]
            falhadas += 1
    db.commit()
    return {"enviadas": enviadas, "falhadas": falhadas}
