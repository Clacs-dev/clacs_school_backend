"""Regras de negócio de cobranças e pagamentos (equivalente portátil de vw_cobranca_detalhe)."""
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.common import ano_activo, dinheiro, soma_meses
from app.models import (
    AdesaoServico, Alerta, Aluno, AlunoEncarregado, Classe, Cobranca, Desconto, Encarregado, Matricula,
    MatriculaDesconto, Mensagem, MetodoPagamento, MultaRegra, Pagamento, PagamentoAlocacao, Parametro,
    PlanoPagamento, RegraAutomacao, Servico, TabelaPreco, Turma,
)

ABERTAS = ("pendente", "parcial")
CENT = Decimal("0.01")


def _pago_subquery():
    return (select(PagamentoAlocacao.cobranca_id.label("cid"), func.sum(PagamentoAlocacao.valor).label("pago"))
            .join(Pagamento, Pagamento.id == PagamentoAlocacao.pagamento_id)
            .where(Pagamento.estado == "confirmado")
            .group_by(PagamentoAlocacao.cobranca_id).subquery())


def cobrancas_detalhe(db: Session, est: int, *, turma_id: int | None = None, servico_id: int | None = None,
                      categoria: str | None = None, estado: str | None = None, q: str | None = None,
                      aluno_id: int | None = None, encarregado_id: int | None = None,
                      competencia: date | None = None, ate_competencia: date | None = None,
                      hoje: date | None = None) -> list[dict]:
    """Lista de cobranças com aluno, turma, encarregado, valor pago, em aberto e dias de atraso.

    estado: 'pago' | 'divida' (pendente/parcial) | 'atraso' (em dívida e vencida) | um estado literal.
    """
    hoje = hoje or date.today()
    pago = _pago_subquery()
    stmt = (select(Cobranca, Aluno.nome_completo, Turma.id, Turma.designacao, Encarregado.nome_completo,
                   Encarregado.telefone, Servico.nome, Servico.categoria, func.coalesce(pago.c.pago, 0))
            .join(Aluno, Aluno.id == Cobranca.aluno_id)
            .join(Matricula, Matricula.id == Cobranca.matricula_id)
            .join(Turma, Turma.id == Matricula.turma_id)
            .join(Servico, Servico.id == Cobranca.servico_id)
            .outerjoin(Encarregado, Encarregado.id == Cobranca.encarregado_id)
            .outerjoin(pago, pago.c.cid == Cobranca.id)
            .where(Cobranca.estabelecimento_id == est, Cobranca.estado != "anulada"))
    if turma_id:
        stmt = stmt.where(Turma.id == turma_id)
    if servico_id:
        stmt = stmt.where(Servico.id == servico_id)
    if categoria:
        stmt = stmt.where(Servico.categoria == categoria)
    if aluno_id:
        stmt = stmt.where(Cobranca.aluno_id == aluno_id)
    if encarregado_id:
        stmt = stmt.where(Cobranca.encarregado_id == encarregado_id)
    if competencia:
        stmt = stmt.where(Cobranca.competencia == competencia)
    if ate_competencia:
        stmt = stmt.where(Cobranca.competencia <= ate_competencia)
    if q:
        stmt = stmt.where(Aluno.nome_completo.ilike(f"%{q.strip()}%"))
    if estado == "pago":
        stmt = stmt.where(Cobranca.estado == "paga")
    elif estado == "divida":
        stmt = stmt.where(Cobranca.estado.in_(ABERTAS))
    elif estado == "atraso":
        stmt = stmt.where(Cobranca.estado.in_(ABERTAS), Cobranca.vencimento < hoje)
    elif estado:
        stmt = stmt.where(Cobranca.estado == estado)
    stmt = stmt.order_by(Turma.designacao, Aluno.nome_completo, Cobranca.competencia, Servico.nome)

    linhas = []
    for c, aluno, t_id, turma, ee, tel, servico, cat, valor_pago in db.execute(stmt):
        valor_pago = Decimal(valor_pago or 0)
        total = c.valor_base - c.valor_desconto + c.valor_multa  # valor_total pode ainda não estar carregado
        aberto = total - valor_pago
        atraso = max(0, (hoje - c.vencimento).days) if c.estado in ABERTAS else 0
        linhas.append({
            "cobranca_id": c.id, "aluno_id": c.aluno_id, "aluno": aluno, "turma_id": t_id, "turma": turma,
            "encarregado_id": c.encarregado_id, "encarregado": ee, "telefone": tel,
            "servico_id": c.servico_id, "servico": servico, "categoria": cat,
            "competencia": c.competencia, "vencimento": c.vencimento, "descricao": c.descricao,
            "valor_base": dinheiro(c.valor_base), "valor_desconto": dinheiro(c.valor_desconto),
            "valor_multa": dinheiro(c.valor_multa), "valor_total": dinheiro(total),
            "valor_pago": dinheiro(valor_pago), "valor_em_aberto": dinheiro(aberto),
            "estado": c.estado, "dias_atraso": atraso,
        })
    return linhas


def resumo(linhas: list[dict]) -> dict:
    total = sum(x["valor_total"] for x in linhas)
    pago = sum(x["valor_pago"] for x in linhas)
    atraso = [x for x in linhas if x["dias_atraso"] > 0 and x["valor_em_aberto"] > 0]
    return {
        "registos": len(linhas), "facturado": total, "recebido": pago,
        "em_aberto": sum(x["valor_em_aberto"] for x in linhas),
        "em_atraso": sum(x["valor_em_aberto"] for x in atraso),
        "alunos_em_incumprimento": len({x["aluno_id"] for x in atraso}),
        "taxa_cobranca": round(100 * pago / total, 1) if total else 0.0,
    }


# ---------------------------------------------------------------- geração mensal
def _dia_vencimento(db: Session, est: int) -> int:
    p = db.get(Parametro, (est, "dia_vencimento"))
    if p is not None:
        return int(p.valor)
    plano = db.scalar(select(PlanoPagamento).where(PlanoPagamento.estabelecimento_id == est).limit(1))
    return plano.dia_vencimento if plano else 10


def _vencimento(competencia: date, dia: int) -> date:
    fim = (soma_meses(competencia, 1) - competencia).days
    return competencia.replace(day=min(dia, fim))


def preco(db: Session, servico_id: int, ano_id: int, classe_id: int | None) -> Decimal | None:
    linhas = db.scalars(select(TabelaPreco).where(
        TabelaPreco.servico_id == servico_id, TabelaPreco.ano_id == ano_id,
        or_(TabelaPreco.classe_id == classe_id, TabelaPreco.classe_id.is_(None)))).all()
    especifico = next((p for p in linhas if p.classe_id is not None), None)
    escolhido = especifico or (linhas[0] if linhas else None)
    return escolhido.valor if escolhido else None


def encarregado_financeiro(db: Session, aluno_id: int) -> int | None:
    ligacoes = db.scalars(select(AlunoEncarregado).where(AlunoEncarregado.aluno_id == aluno_id)).all()
    for criterio in (lambda x: x.responsavel_financeiro, lambda x: x.principal, lambda x: True):
        for x in ligacoes:
            if criterio(x):
                return x.encarregado_id
    return None


def _desconto(db: Session, matricula_id: int, servico_id: int, base: Decimal, competencia: date,
              irmaos: Desconto | None = None) -> Decimal:
    """Descontos atribuídos à matrícula, mais o desconto de irmãos automático quando `irmaos` é dado."""
    total = Decimal(0)
    descontos = db.scalars(
        select(Desconto).join(MatriculaDesconto, MatriculaDesconto.desconto_id == Desconto.id)
        .where(MatriculaDesconto.matricula_id == matricula_id,
               or_(MatriculaDesconto.valido_ate.is_(None), MatriculaDesconto.valido_ate >= competencia),
               or_(Desconto.servico_id.is_(None), Desconto.servico_id == servico_id))).all()
    if irmaos and irmaos not in descontos and irmaos.servico_id in (None, servico_id):
        descontos.append(irmaos)
    for d in descontos:
        total += base * d.valor / 100 if d.tipo == "percentual" else d.valor
    return min(base, total).quantize(CENT, ROUND_HALF_UP)


def meses_do_plano(db: Session, est: int) -> list[int] | None:
    """Meses (1–12) em que há prestação, segundo o plano de pagamento; None = sem plano, todos os meses."""
    plano = _plano(db, est)
    if not plano:
        return None
    return [(plano.mes_inicio - 1 + i) % 12 + 1 for i in range(plano.num_prestacoes)]


def _irmaos(db: Session, est: int, matriculas: list[Matricula]) -> set[int]:
    """Matrículas que recebem desconto de irmãos: todas menos a primeira de cada responsável financeiro."""
    por_ee: dict[int, list[int]] = {}
    for m in sorted(matriculas, key=lambda m: m.id):
        ee = encarregado_financeiro(db, m.aluno_id)
        if ee:
            por_ee.setdefault(ee, []).append(m.id)
    return {mid for ids in por_ee.values() for mid in ids[1:]}


def _talvez(v: Decimal | None) -> float | None:
    return None if v is None else float(v)


def _propinas(db: Session, est: int) -> list[Servico]:
    return db.scalars(select(Servico).where(Servico.estabelecimento_id == est, Servico.activo,
                                            Servico.categoria == "propina", Servico.periodicidade == "mensal")
                      .order_by(Servico.id)).all()


def _regra_multa(db: Session, est: int, servico_id: int) -> MultaRegra | None:
    regras = db.scalars(select(MultaRegra).where(MultaRegra.estabelecimento_id == est)).all()
    return next((r for r in regras if r.servico_id == servico_id), None) or \
        next((r for r in regras if r.servico_id is None), None)


def configuracao_cobranca(db: Session, est: int) -> dict:
    """O que o mapa de cobrança precisa antes de gerar: a propina, o preço de cada classe com alunos e a multa."""
    ano = ano_activo(db, est, obrigatorio=False)
    if ano is None:
        return {"ano": None, "propinas": [], "dia_vencimento": _dia_vencimento(db, est),
                "pendentes": ["ano lectivo activo"], "pronto": False}
    classes = db.execute(select(Classe.id, Classe.designacao).join(Turma, Turma.classe_id == Classe.id)
                         .join(Matricula, Matricula.turma_id == Turma.id)
                         .where(Matricula.estabelecimento_id == est, Matricula.ano_id == ano.id,
                                Matricula.estado == "activa")
                         .distinct().order_by(Classe.designacao)).all()
    pendentes, propinas = [], []
    servicos = _propinas(db, est)
    if not servicos:
        pendentes.append("serviço de propina (categoria propina, mensal)")
    for s in servicos:
        precos = [{"classe_id": cid, "classe": nome, "valor": _talvez(preco(db, s.id, ano.id, cid))}
                  for cid, nome in classes]
        sem = [p["classe"] for p in precos if p["valor"] is None]
        if sem:
            pendentes.append(f"preço da {s.nome} para {', '.join(sem)}")
        regra = _regra_multa(db, est, s.id)
        if regra is None:
            pendentes.append(f"percentagem da multa da {s.nome}")
        multa = regra and {"tipo": regra.tipo, "valor": float(regra.valor), "dias_tolerancia": regra.dias_tolerancia,
                           "maximo": _talvez(regra.maximo)}
        propinas.append({"servico_id": s.id, "nome": s.nome, "precos": precos, "multa": multa})
    plano = _plano(db, est)
    return {"ano": ano.designacao, "propinas": propinas, "dia_vencimento": _dia_vencimento(db, est),
            "plano": plano and {"prestacoes": plano.num_prestacoes, "mes_inicio": plano.mes_inicio,
                                "dia_vencimento": plano.dia_vencimento, "meses": meses_do_plano(db, est)},
            "alertas_dias": sorted((r.dias_offset or 0 for r in _regras_alerta(db, est) if r.activa), reverse=True),
            "pendentes": pendentes, "pronto": not pendentes}


# Lembretes do mapa de cobrança: dias antes do vencimento (dia 10) em que o encarregado é avisado.
# 20 e 15 dias antes ≈ dias 20 e 25 do mês anterior; 5 dias antes; 0 = no próprio dia do vencimento.
ALERTAS_PADRAO = [20, 15, 5, 0]
ORIGEM_ALERTAS = "mapa_cobranca"


def _plano(db: Session, est: int) -> PlanoPagamento | None:
    return db.scalar(select(PlanoPagamento).where(PlanoPagamento.estabelecimento_id == est)
                     .order_by(PlanoPagamento.id).limit(1))


def _regras_alerta(db: Session, est: int) -> list[RegraAutomacao]:
    regras = db.scalars(select(RegraAutomacao).where(RegraAutomacao.estabelecimento_id == est,
                                                     RegraAutomacao.evento == "antes_vencimento")).all()
    return [r for r in regras if (r.condicao or {}).get("origem") == ORIGEM_ALERTAS]


def _gravar_alertas(db: Session, est: int, dias: list[int]) -> None:
    """Um lembrete por dia pedido (antes do vencimento, por WhatsApp); os que deixaram de ser pedidos desligam-se."""
    existentes = {r.dias_offset or 0: r for r in _regras_alerta(db, est)}
    for d, r in existentes.items():
        r.activa = d in dias
    for d in set(dias) - set(existentes):
        db.add(RegraAutomacao(
            estabelecimento_id=est, evento="antes_vencimento", dias_offset=d, canais=["whatsapp"], activa=True,
            nome="Propina: lembrete no dia do vencimento" if d == 0 else f"Propina: lembrete {d} dias antes",
            condicao={"categoria": "propina", "origem": ORIGEM_ALERTAS}, destino="encarregado"))


def configurar_cobranca(db: Session, est: int, servico_id: int | None = None, valor: Decimal | None = None,
                        precos: list[tuple[int, Decimal]] | None = None, multa_percentual: Decimal | None = None,
                        dias_tolerancia: int | None = None, multa_maximo: Decimal | None = None,
                        prestacoes: int | None = None, mes_inicio: int | None = None,
                        dia_vencimento: int | None = None, alertas_dias: list[int] | None = None) -> Servico | None:
    """Grava o que o "Gerar" trouxe: plano (prestações e vencimento), alertas, preço da propina e regra de multa."""
    if prestacoes is not None or mes_inicio is not None or dia_vencimento is not None:
        plano = _plano(db, est)
        if plano is None:
            if prestacoes is None or mes_inicio is None:
                raise HTTPException(422, "Para criar o plano indique prestacoes e mes_inicio")
            plano = PlanoPagamento(estabelecimento_id=est, designacao="", num_prestacoes=prestacoes,
                                   mes_inicio=mes_inicio, dia_vencimento=dia_vencimento or 10)
            db.add(plano)
        plano.num_prestacoes = prestacoes or plano.num_prestacoes
        plano.mes_inicio = mes_inicio or plano.mes_inicio
        plano.dia_vencimento = dia_vencimento or plano.dia_vencimento
        plano.designacao = f"{plano.num_prestacoes} prestações"
        parametro = db.get(Parametro, (est, "dia_vencimento"))
        if parametro is not None and dia_vencimento is not None:  # o parâmetro manda sobre o plano: acompanha-o
            parametro.valor = dia_vencimento
    if alertas_dias is not None:
        _gravar_alertas(db, est, alertas_dias)
    elif not _regras_alerta(db, est):
        _gravar_alertas(db, est, ALERTAS_PADRAO)
    if valor is None and not precos and multa_percentual is None and dias_tolerancia is None and multa_maximo is None:
        db.flush()
        return None
    servicos = _propinas(db, est)
    if servico_id is not None:
        s = next((x for x in servicos if x.id == servico_id), None)
        if s is None:
            raise HTTPException(422, f"O serviço {servico_id} não é uma propina mensal activa")
    elif len(servicos) == 1:
        s = servicos[0]
    elif not servicos:
        raise HTTPException(422, "Crie primeiro o serviço de propina (categoria propina, periodicidade mensal)")
    else:
        raise HTTPException(422, "Há mais de uma propina mensal: indique servico_id")
    ano = ano_activo(db, est)

    def gravar_preco(classe_id: int | None, v: Decimal) -> None:
        p = db.scalar(select(TabelaPreco).where(
            TabelaPreco.servico_id == s.id, TabelaPreco.ano_id == ano.id,
            TabelaPreco.classe_id.is_(None) if classe_id is None else TabelaPreco.classe_id == classe_id))
        if p is None:
            p = TabelaPreco(estabelecimento_id=est, servico_id=s.id, ano_id=ano.id, classe_id=classe_id)
            db.add(p)
        p.valor = v

    if valor is not None:  # o mesmo preço para todas as classes, também as que tinham preço próprio
        gravar_preco(None, valor)
        for p in db.scalars(select(TabelaPreco).where(TabelaPreco.servico_id == s.id, TabelaPreco.ano_id == ano.id,
                                                      TabelaPreco.classe_id.is_not(None))):
            p.valor = valor
    for classe_id, v in precos or []:
        if db.get(Classe, classe_id) is None:
            raise HTTPException(422, f"A classe {classe_id} não existe")
        gravar_preco(classe_id, v)

    if multa_percentual is not None or dias_tolerancia is not None or multa_maximo is not None:
        regra = db.scalar(select(MultaRegra).where(MultaRegra.estabelecimento_id == est, MultaRegra.servico_id == s.id))
        if regra is None:
            if multa_percentual is None:
                raise HTTPException(422, "Indique a percentagem da multa (multa_percentual)")
            regra = MultaRegra(estabelecimento_id=est, servico_id=s.id, tipo="percentual", dias_tolerancia=0)
            db.add(regra)
        if multa_percentual is not None:
            regra.tipo, regra.valor = "percentual", multa_percentual
        if dias_tolerancia is not None:
            regra.dias_tolerancia = dias_tolerancia
        if multa_maximo is not None:
            regra.maximo = multa_maximo
    db.flush()
    return s


def gerar_ano(db: Session, est: int, desde: date) -> dict:
    """O mapa de cobrança de uma vez: todas as prestações do plano, de `desde` até ao fim do ano lectivo activo."""
    ano = ano_activo(db, est)
    meses, mes = [], max(desde.replace(day=1), ano.data_inicio.replace(day=1))
    while mes <= ano.data_fim:
        r = gerar_cobrancas(db, est, mes)
        if not r["fora_do_plano"]:
            meses.append(r)
        mes = soma_meses(mes, 1)
    if not meses:
        return gerar_cobrancas(db, est, desde) | {"meses": []}
    return {"competencia": meses[0]["competencia"], "criadas": sum(r["criadas"] for r in meses),
            "ja_existentes": sum(r["ja_existentes"] for r in meses),
            "servicos_sem_preco": sorted({s for r in meses for s in r["servicos_sem_preco"]}),
            "com_desconto_irmaos": max(r["com_desconto_irmaos"] for r in meses), "fora_do_plano": False,
            "meses": [{"competencia": r["competencia"], "criadas": r["criadas"]} for r in meses]}


def gerar_cobrancas(db: Session, est: int, competencia: date) -> dict:
    """Cria as cobranças mensais (propina + serviços aderidos) das matrículas activas. Idempotente.

    Só gera nos meses do plano de pagamento e dentro do ano lectivo activo. Com o parâmetro
    desconto_irmaos_automatico (activo por omissão) e um desconto de origem "irmaos", aplica-o à propina
    do segundo educando em diante de cada encarregado.
    """
    competencia = competencia.replace(day=1)
    ano = ano_activo(db, est)
    meses = meses_do_plano(db, est)
    fora = None
    if not (ano.data_inicio.replace(day=1) <= competencia <= ano.data_fim):
        fora = f"{competencia:%m/%Y} está fora do ano lectivo {ano.designacao}"
    elif meses is not None and competencia.month not in meses:
        fora = f"{competencia:%m/%Y} não é mês de prestação no plano de pagamento"
    if fora:
        return {"competencia": competencia, "criadas": 0, "ja_existentes": 0, "servicos_sem_preco": [],
                "fora_do_plano": True, "motivo": fora}
    dia = _dia_vencimento(db, est)
    propinas = _propinas(db, est)
    fim_mes = soma_meses(competencia, 1)
    existentes = set(db.execute(select(Cobranca.matricula_id, Cobranca.servico_id)
                                .where(Cobranca.estabelecimento_id == est,
                                       Cobranca.competencia == competencia)).tuples())
    criadas, sem_preco = 0, set()
    matriculas = db.execute(select(Matricula, Turma.classe_id).join(Turma, Turma.id == Matricula.turma_id)
                            .where(Matricula.estabelecimento_id == est, Matricula.ano_id == ano.id,
                                   Matricula.estado == "activa")).all()
    automatico = db.get(Parametro, (est, "desconto_irmaos_automatico"))
    desc_irmaos = None if (automatico is not None and not automatico.valor) else db.scalar(
        select(Desconto).where(Desconto.estabelecimento_id == est, Desconto.origem == "irmaos").limit(1))
    com_irmaos = _irmaos(db, est, [m for m, _ in matriculas]) if desc_irmaos else set()
    for m, classe_id in matriculas:
        servicos = list(propinas)
        servicos += db.scalars(
            select(Servico).join(AdesaoServico, AdesaoServico.servico_id == Servico.id)
            .where(AdesaoServico.matricula_id == m.id, AdesaoServico.estado == "activa",
                   AdesaoServico.data_inicio < fim_mes,
                   or_(AdesaoServico.data_fim.is_(None), AdesaoServico.data_fim >= competencia),
                   Servico.activo, Servico.periodicidade == "mensal")).all()
        ee = encarregado_financeiro(db, m.aluno_id)
        for s in {s.id: s for s in servicos}.values():
            if (m.id, s.id) in existentes:
                continue
            base = preco(db, s.id, ano.id, classe_id)
            if base is None:
                sem_preco.add(s.nome)
                continue
            db.add(Cobranca(
                estabelecimento_id=est, matricula_id=m.id, aluno_id=m.aluno_id, encarregado_id=ee,
                servico_id=s.id, competencia=competencia, descricao=f"{s.nome} {competencia:%m/%Y}",
                valor_base=base, valor_desconto=_desconto(db, m.id, s.id, base, competencia,
                                                          desc_irmaos if m.id in com_irmaos else None),
                vencimento=_vencimento(competencia, dia)))
            criadas += 1
    db.commit()
    return {"competencia": competencia, "criadas": criadas, "ja_existentes": len(existentes),
            "servicos_sem_preco": sorted(sem_preco), "com_desconto_irmaos": len(com_irmaos), "fora_do_plano": False}


# ---------------------------------------------------------------- pagamentos
def valor_em_aberto(db: Session, c: Cobranca) -> Decimal:
    pago = db.scalar(select(func.coalesce(func.sum(PagamentoAlocacao.valor), 0))
                     .join(Pagamento, Pagamento.id == PagamentoAlocacao.pagamento_id)
                     .where(PagamentoAlocacao.cobranca_id == c.id, Pagamento.estado == "confirmado"))
    total = c.valor_base - c.valor_desconto + c.valor_multa
    return (total - Decimal(pago)).quantize(CENT)


def actualizar_estado(db: Session, c: Cobranca) -> None:
    if c.estado == "anulada":
        return
    aberto = valor_em_aberto(db, c)
    total = c.valor_base - c.valor_desconto + c.valor_multa
    c.estado = "paga" if aberto <= 0 else ("parcial" if aberto < total else "pendente")


def registar_pagamento(db: Session, est: int, utilizador_id: int | None, *, metodo_id: int, valor: Decimal,
                       encarregado_id: int | None = None, cobranca_ids: list[int] | None = None,
                       alocacoes: list[tuple[int, Decimal]] | None = None, data_pagamento: date | None = None,
                       referencia: str | None = None, comprovativo_url: str | None = None,
                       estado: str = "confirmado") -> Pagamento:
    """Regista um pagamento e distribui-o pelas cobranças (as mais antigas primeiro, se não indicado)."""
    valor = Decimal(valor).quantize(CENT)
    metodo = db.get(MetodoPagamento, metodo_id)
    if not metodo or metodo.estabelecimento_id != est:
        raise HTTPException(404, "Método de pagamento não encontrado")

    if alocacoes:
        destino = [(db.get(Cobranca, cid), Decimal(v).quantize(CENT)) for cid, v in alocacoes]
        if sum(v for _, v in destino) != valor:
            raise HTTPException(422, "A soma das alocações tem de ser igual ao valor pago")
    else:
        stmt = select(Cobranca).where(Cobranca.estabelecimento_id == est, Cobranca.estado.in_(ABERTAS))
        if cobranca_ids:
            stmt = stmt.where(Cobranca.id.in_(cobranca_ids))
        elif encarregado_id:
            stmt = stmt.where(Cobranca.encarregado_id == encarregado_id)
        else:
            raise HTTPException(422, "Indique as cobranças ou o encarregado")
        destino, resto = [], valor
        for c in db.scalars(stmt.order_by(Cobranca.vencimento, Cobranca.id)):
            if resto <= 0:
                break
            parte = min(resto, valor_em_aberto(db, c))
            if parte > 0:
                destino.append((c, parte))
                resto -= parte
        if resto > 0:
            raise HTTPException(422, f"O valor excede a dívida em {resto} Kz")

    for c, v in destino:
        if c is None or c.estabelecimento_id != est:
            raise HTTPException(404, "Cobrança não encontrada")
        if c.estado not in ABERTAS:
            raise HTTPException(409, f"A cobrança {c.id} não está em aberto")
        if v <= 0 or v > valor_em_aberto(db, c):
            raise HTTPException(422, f"Valor inválido para a cobrança {c.id}")

    if encarregado_id is None and destino:
        encarregado_id = destino[0][0].encarregado_id
    pg = Pagamento(estabelecimento_id=est, encarregado_id=encarregado_id, metodo_id=metodo_id, valor=valor,
                   data_pagamento=data_pagamento or date.today(), referencia=referencia,
                   comprovativo_url=comprovativo_url, estado=estado, registado_por=utilizador_id)
    db.add(pg)
    db.flush()
    for c, v in destino:
        db.add(PagamentoAlocacao(pagamento_id=pg.id, cobranca_id=c.id, valor=v))
    db.flush()
    for c, _ in destino:
        actualizar_estado(db, c)
    db.commit()
    return pg


def _kz(v) -> str:
    return f"{float(v):,.0f}".replace(",", " ") + " Kz"


def avisar_comprovativo(db: Session, pg: Pagamento, aluno_id: int | None) -> None:
    """Alerta para a escola: há um comprovativo à espera de verificação (fecha-se ao confirmar ou rejeitar)."""
    ee = db.get(Encarregado, pg.encarregado_id) if pg.encarregado_id else None
    db.add(Alerta(estabelecimento_id=pg.estabelecimento_id, tipo="comprovativo_pagamento", aluno_id=aluno_id,
                  gravidade="media", dados={"pagamento_id": pg.id, "valor": float(pg.valor)},
                  mensagem=f"{ee.nome_completo if ee else 'Um encarregado'} enviou um comprovativo de "
                           f"{_kz(pg.valor)} para verificar."))


def mudar_estado_pagamento(db: Session, pg: Pagamento, estado: str, tratado_por: int | None = None) -> None:
    """Confirma ou rejeita um pagamento pendente (ex.: comprovativo enviado pelo encarregado).

    Fecha o alerta do comprovativo e avisa o encarregado na app e por WhatsApp."""
    pg.estado = estado
    db.flush()
    cids = db.scalars(select(PagamentoAlocacao.cobranca_id).where(PagamentoAlocacao.pagamento_id == pg.id)).all()
    for cid in cids:
        actualizar_estado(db, db.get(Cobranca, cid))
    avisos = db.scalars(select(Alerta).where(Alerta.estabelecimento_id == pg.estabelecimento_id,
                                             Alerta.tipo == "comprovativo_pagamento",
                                             Alerta.estado.in_(("novo", "visto")))).all()
    for a in avisos:
        if (a.dados or {}).get("pagamento_id") == pg.id:
            a.estado, a.tratado_por = "tratado", tratado_por
    if avisos and pg.encarregado_id and estado in ("confirmado", "rejeitado"):
        texto = (f"O seu pagamento de {_kz(pg.valor)} foi confirmado pela escola. Obrigado."
                 if estado == "confirmado" else
                 f"O comprovativo do pagamento de {_kz(pg.valor)} não foi aceite. Por favor contacte a secretaria.")
        aluno_id = next((a.aluno_id for a in avisos if (a.dados or {}).get("pagamento_id") == pg.id), None)
        for canal in ("app", "whatsapp"):
            db.add(Mensagem(estabelecimento_id=pg.estabelecimento_id, encarregado_id=pg.encarregado_id,
                            aluno_id=aluno_id, canal=canal, assunto="Pagamento", corpo=texto))
    db.commit()


def aplicar_multas(db: Session, est: int, hoje: date | None = None) -> int:
    hoje = hoje or date.today()
    regras = db.scalars(select(MultaRegra).where(MultaRegra.estabelecimento_id == est)).all()
    if not regras:
        return 0
    alteradas = 0
    for c in db.scalars(select(Cobranca).where(Cobranca.estabelecimento_id == est,
                                               Cobranca.estado.in_(ABERTAS), Cobranca.vencimento < hoje)):
        regra = next((r for r in regras if r.servico_id == c.servico_id), None) or \
                next((r for r in regras if r.servico_id is None), None)
        if regra is None or (hoje - c.vencimento).days <= regra.dias_tolerancia:
            continue
        multa = c.valor_base * regra.valor / 100 if regra.tipo == "percentual" else regra.valor
        if regra.maximo is not None:
            multa = min(multa, regra.maximo)
        multa = multa.quantize(CENT, ROUND_HALF_UP)
        if multa > c.valor_multa:
            c.valor_multa = multa
            alteradas += 1
    db.commit()
    return alteradas


def resumo_por_turma(db: Session, est: int, ate_competencia: date | None = None) -> list[dict]:
    grupos: dict[int, dict] = {}
    for x in cobrancas_detalhe(db, est, ate_competencia=ate_competencia):
        g = grupos.setdefault(x["turma_id"], {"turma_id": x["turma_id"], "turma": x["turma"], "alunos": set(),
                                              "facturado": 0.0, "recebido": 0.0, "em_atraso": 0.0})
        g["alunos"].add(x["aluno_id"])
        g["facturado"] += x["valor_total"]
        g["recebido"] += x["valor_pago"]
        if x["dias_atraso"] > 0:
            g["em_atraso"] += x["valor_em_aberto"]
    res = []
    for g in grupos.values():
        g["alunos"] = len(g["alunos"])
        g["taxa_cobranca"] = round(100 * g["recebido"] / g["facturado"], 1) if g["facturado"] else 0.0
        res.append(g)
    return sorted(res, key=lambda g: g["turma"])


def devedores(db: Session, est: int) -> list[dict]:
    """Quem tem cobranças vencidas por pagar, do maior para o menor valor."""
    grupos: dict[int, dict] = {}
    for x in cobrancas_detalhe(db, est, estado="atraso"):
        g = grupos.setdefault(x["aluno_id"], {k: x[k] for k in ("aluno_id", "aluno", "turma", "encarregado_id",
                                                                 "encarregado", "telefone")}
                              | {"cobrancas_em_atraso": 0, "total_em_divida": 0.0, "maior_atraso": 0})
        g["cobrancas_em_atraso"] += 1
        g["total_em_divida"] += x["valor_em_aberto"]
        g["maior_atraso"] = max(g["maior_atraso"], x["dias_atraso"])
    return sorted(grupos.values(), key=lambda g: -g["total_em_divida"])
