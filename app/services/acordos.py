"""Acordos de pagamento: dívidas (cobranças em aberto) pagas em prestações.

Os pagamentos continuam a ser alocados às cobranças (como sempre); o acordo só acompanha: o que se pagou a essas
cobranças depois de o acordo ser criado abate as prestações por ordem de vencimento. Uma prestação vencida e não
coberta põe o acordo em incumprimento. Enquanto o acordo está activo (e se assim foi acordado) as cobranças não
recebem multas. Quando o saldo chega a zero, o acordo fica cumprido.
"""
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.common import as_dict, dinheiro, soma_meses
from app.models import (
    AcordoCobranca, AcordoModelo, AcordoPagamento, AcordoPrestacao, AlunoEncarregado, Cobranca, Encarregado, Utilizador,
)
from app.services import financeiro as fin

CENT = Decimal("0.01")
VIGENTES = ("proposto", "activo")


def _historico(a: AcordoPagamento, por: str | None, nota: str) -> None:
    a.historico = [*(a.historico or []), {"em": datetime.now(timezone.utc).isoformat(timespec="seconds"), "estado": a.estado,
                                          "por": por, "nota": nota}]


def _pago(db: Session, c: Cobranca) -> Decimal:
    return c.valor_base - c.valor_desconto + c.valor_multa - fin.valor_em_aberto(db, c)


def cobrancas(db: Session, a: AcordoPagamento) -> list[Cobranca]:
    ids = db.scalars(select(AcordoCobranca.cobranca_id).where(AcordoCobranca.acordo_id == a.id)).all()
    return list(db.scalars(select(Cobranca).where(Cobranca.id.in_(ids)).order_by(Cobranca.vencimento)))


def plano(total: Decimal, num: int, primeiro: date, intervalo_meses: int, entrada_pct: Decimal) -> list[tuple[Decimal, date]]:
    """Prestações: a entrada (se houver) e o resto em partes iguais; a última acerta os cêntimos."""
    if num < 1:
        raise HTTPException(422, "O acordo precisa de pelo menos uma prestação")
    valores = []
    entrada = (total * entrada_pct / 100).quantize(CENT, ROUND_HALF_UP) if entrada_pct else Decimal(0)
    if entrada:
        valores.append(entrada)
        num -= 1
        if num < 1:
            raise HTTPException(422, "Com entrada, o acordo precisa de pelo menos duas prestações")
    parte = ((total - entrada) / num).quantize(CENT, ROUND_HALF_UP)
    valores += [parte] * (num - 1) + [total - entrada - parte * (num - 1)]
    datas, d = [], primeiro
    for i in range(len(valores)):
        datas.append(d if i == 0 else _mais_meses(primeiro, i * intervalo_meses))
    return list(zip(valores, datas))


def _mais_meses(d: date, n: int) -> date:
    alvo = soma_meses(d, n)   # 1.º dia do mês alvo
    for dia in (d.day, 30, 29, 28):
        try:
            return alvo.replace(day=dia)
        except ValueError:
            continue
    return alvo


def criar(db: Session, est: int, por: Utilizador, *, encarregado_id: int, cobranca_ids: list[int], motivo: str,
          modelo_id: int | None, num_prestacoes: int | None, primeiro_vencimento: date, intervalo_meses: int | None,
          entrada_percentual: Decimal | None, suspende_multas: bool | None) -> AcordoPagamento:
    ee = db.get(Encarregado, encarregado_id)
    if ee is None or ee.estabelecimento_id != est:
        raise HTTPException(404, f"Encarregado {encarregado_id} não encontrado")
    educandos = set(db.scalars(select(AlunoEncarregado.aluno_id).where(AlunoEncarregado.encarregado_id == ee.id)))
    lista = list(db.scalars(select(Cobranca).where(Cobranca.id.in_(cobranca_ids), Cobranca.estabelecimento_id == est)))
    if len(lista) != len(set(cobranca_ids)):
        raise HTTPException(404, "Há cobranças que não existem")
    for c in lista:
        if c.estado not in fin.ABERTAS:
            raise HTTPException(409, f"A cobrança {c.id} não está em aberto")
        if c.encarregado_id != ee.id and c.aluno_id not in educandos:
            raise HTTPException(422, f"A cobrança {c.id} não é deste encarregado nem dos seus educandos")
    ocupadas = set(db.scalars(select(AcordoCobranca.cobranca_id).join(AcordoPagamento, AcordoPagamento.id == AcordoCobranca.acordo_id)
                              .where(AcordoCobranca.cobranca_id.in_(cobranca_ids), AcordoPagamento.estado.in_(VIGENTES))))
    if ocupadas:
        raise HTTPException(409, f"Cobranças já incluídas noutro acordo: {sorted(ocupadas)}")
    modelo = None
    if modelo_id is not None:
        modelo = db.get(AcordoModelo, modelo_id)
        if modelo is None or modelo.estabelecimento_id != est or not modelo.activo:
            raise HTTPException(404, f"Modelo de acordo {modelo_id} não encontrado")
    num = num_prestacoes or (modelo.num_prestacoes if modelo else None)
    if not num:
        raise HTTPException(422, "Indique o número de prestações ou um modelo")
    intervalo = intervalo_meses or (modelo.intervalo_meses if modelo else 1)
    entrada = entrada_percentual if entrada_percentual is not None else (modelo.entrada_percentual if modelo else Decimal(0))
    suspende = suspende_multas if suspende_multas is not None else (modelo.suspende_multas if modelo else True)
    total = sum((fin.valor_em_aberto(db, c) for c in lista), Decimal(0))
    if total <= 0:
        raise HTTPException(409, "As cobranças escolhidas não têm valor em dívida")
    alunos = {c.aluno_id for c in lista}
    a = AcordoPagamento(estabelecimento_id=est, encarregado_id=ee.id, aluno_id=alunos.pop() if len(alunos) == 1 else None,
                        modelo_id=modelo.id if modelo else None, motivo=motivo, valor_total=total,
                        pago_inicial=sum((_pago(db, c) for c in lista), Decimal(0)), suspende_multas=suspende,
                        criado_por=por.id)
    db.add(a)
    db.flush()
    db.add_all([AcordoCobranca(acordo_id=a.id, cobranca_id=c.id) for c in lista])
    db.add_all([AcordoPrestacao(acordo_id=a.id, numero=i, valor=v, vencimento=d)
                for i, (v, d) in enumerate(plano(total, num, primeiro_vencimento, intervalo, entrada), start=1)])
    _historico(a, por.nome, f"Proposto: {len(lista)} cobrança(s), {num} prestação(ões). {motivo}")
    return a


def prestacoes(db: Session, a: AcordoPagamento) -> list[AcordoPrestacao]:
    return list(db.scalars(select(AcordoPrestacao).where(AcordoPrestacao.acordo_id == a.id).order_by(AcordoPrestacao.numero)))


def alterar_plano(db: Session, a: AcordoPagamento, por: Utilizador, novas: list[tuple[Decimal, date]], motivo: str) -> None:
    if a.estado not in VIGENTES:
        raise HTTPException(409, f"O acordo está {a.estado}")
    if sum((v for v, _ in novas), Decimal(0)) != a.valor_total:
        raise HTTPException(422, f"A soma das prestações tem de ser {dinheiro(a.valor_total):g}")
    antes = [f"{p.numero}: {dinheiro(p.valor):g} a {p.vencimento}" for p in prestacoes(db, a)]
    for p in prestacoes(db, a):
        db.delete(p)
    db.flush()
    db.add_all([AcordoPrestacao(acordo_id=a.id, numero=i, valor=v, vencimento=d) for i, (v, d) in enumerate(novas, start=1)])
    _historico(a, por.nome, f"Plano alterado ({motivo}). Antes: {'; '.join(antes)}")


def situacao(db: Session, a: AcordoPagamento, hoje: date | None = None) -> dict:
    """Pago, saldo e estado de cada prestação; actualiza o acordo para cumprido quando o saldo chega a zero."""
    hoje = hoje or date.today()
    pago = max(Decimal(0), sum((_pago(db, c) for c in cobrancas(db, a)), Decimal(0)) - a.pago_inicial)
    resto, linhas, atrasadas = pago, [], 0
    for p in prestacoes(db, a):
        coberto = min(resto, p.valor)
        resto -= coberto
        estado = "paga" if coberto >= p.valor else ("atrasada" if p.vencimento < hoje else ("parcial" if coberto else "pendente"))
        atrasadas += estado == "atrasada"
        linhas.append({**as_dict(p), "pago": dinheiro(coberto), "estado": estado})
    saldo = max(Decimal(0), a.valor_total - pago)
    if a.estado == "activo" and saldo == 0:
        a.estado = "cumprido"
        _historico(a, None, "Cumprido: todas as prestações pagas")
    return {"pago": dinheiro(pago), "saldo": dinheiro(saldo), "incumprimento": a.estado == "activo" and atrasadas > 0,
            "prestacoes_atrasadas": atrasadas, "prestacoes": linhas}


def detalhe(db: Session, a: AcordoPagamento) -> dict:
    ee = db.get(Encarregado, a.encarregado_id)
    s = situacao(db, a)   # primeiro: pode passar o acordo a cumprido
    return {**as_dict(a), "encarregado": ee.nome_completo, **s,
            "cobrancas": [{"id": c.id, "descricao": c.descricao, "vencimento": c.vencimento,
                           "valor_em_aberto": dinheiro(fin.valor_em_aberto(db, c))} for c in cobrancas(db, a)]}
