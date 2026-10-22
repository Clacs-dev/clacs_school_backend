from datetime import date
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import respostas as R
from app.common import ano_activo, aplicar, as_dict, obter
from app.database import get_db
from app.exportar import tabela
from app.models import (
    Classe, Cobranca, Desconto, Encarregado, Matricula, MetodoPagamento, MultaRegra, Pagamento,
    PagamentoAlocacao, PlanoPagamento, Servico, TabelaPreco, Turma,
)
from app.schemas import (
    CobrancaAvulsaIn, DescontoIn, GerarIn, LembreteIn, MetodoIn, MultaIn, PagamentoIn, PlanoIn, PrecoIn, ServicoIn,
)
from app.security import Contexto, auditar, exige
from app.services import financeiro as fin
from app.services.comunicacao import enfileirar_lembretes

router = APIRouter(prefix="/api", tags=["Propinas e cobranças"])
ver, criar, editar = exige("propinas"), exige("propinas", "criar"), exige("propinas", "editar")

COLUNAS_COBRANCA = [("aluno", "Aluno"), ("turma", "Turma"), ("encarregado", "Encarregado"), ("telefone", "Telefone"),
                    ("servico", "Serviço"), ("competencia", "Competência"), ("vencimento", "Vencimento"),
                    ("valor_total", "Valor (Kz)"), ("valor_pago", "Pago (Kz)"), ("valor_em_aberto", "Em aberto (Kz)"),
                    ("estado", "Estado"), ("dias_atraso", "Dias de atraso")]


def gravar(db: Session, obj, msg="Registo duplicado"):
    try:
        db.add(obj)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, msg)
    return as_dict(obj)


# ---------------------------------------------------------------- catálogo
@router.get("/servicos", response_model=list[R.ServicoComPreco])
def servicos(ctx: Contexto = Depends(exige("propinas")), db: Session = Depends(get_db)):
    ano = ano_activo(db, ctx.estabelecimento_id, obrigatorio=False)
    precos = {}
    if ano:
        for p in db.scalars(select(TabelaPreco).where(TabelaPreco.ano_id == ano.id, TabelaPreco.classe_id.is_(None))):
            precos[p.servico_id] = float(p.valor)
    return [{**as_dict(s), "preco": precos.get(s.id)}
            for s in db.scalars(select(Servico).where(Servico.estabelecimento_id == ctx.estabelecimento_id)
                                .order_by(Servico.categoria, Servico.nome))]


@router.post("/servicos", response_model=R.Servico, status_code=201)
def criar_servico(dados: ServicoIn, ctx: Contexto = Depends(criar), db: Session = Depends(get_db)):
    return gravar(db, Servico(estabelecimento_id=ctx.estabelecimento_id, **dados.model_dump()), "Código de serviço já usado")


@router.patch("/servicos/{sid}", response_model=R.Servico)
def editar_servico(sid: int, dados: ServicoIn, ctx: Contexto = Depends(editar), db: Session = Depends(get_db)):
    s = obter(db, Servico, sid, ctx.estabelecimento_id, "Serviço")
    aplicar(s, dados.model_dump(exclude_unset=True))
    return gravar(db, s, "Código de serviço já usado")


@router.get("/precos", response_model=list[R.PrecoComNomes])
def precos(ano_id: int | None = None, ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    ano_id = ano_id or ano_activo(db, ctx.estabelecimento_id).id
    rows = db.execute(select(TabelaPreco, Servico.nome, Classe.designacao)
                      .join(Servico, Servico.id == TabelaPreco.servico_id)
                      .outerjoin(Classe, Classe.id == TabelaPreco.classe_id)
                      .where(TabelaPreco.estabelecimento_id == ctx.estabelecimento_id, TabelaPreco.ano_id == ano_id))
    return [{**as_dict(p), "servico": s, "classe": c or "Todas"} for p, s, c in rows]


@router.put("/precos", response_model=R.TabelaPreco, summary="Define (ou actualiza) o preço de um serviço no ano")
def definir_preco(dados: PrecoIn, ctx: Contexto = Depends(editar), db: Session = Depends(get_db)):
    est = ctx.estabelecimento_id
    ano_id = dados.ano_id or ano_activo(db, est).id
    obter(db, Servico, dados.servico_id, est, "Serviço")
    p = db.scalar(select(TabelaPreco).where(TabelaPreco.servico_id == dados.servico_id, TabelaPreco.ano_id == ano_id,
                                            TabelaPreco.classe_id.is_(None) if dados.classe_id is None
                                            else TabelaPreco.classe_id == dados.classe_id))
    p = p or TabelaPreco(estabelecimento_id=est, servico_id=dados.servico_id, ano_id=ano_id, classe_id=dados.classe_id)
    p.valor = dados.valor
    auditar(db, ctx, "tabela_preco", p.id, "update", dados.model_dump(mode="json"))
    return gravar(db, p)


@router.get("/planos-pagamento", response_model=list[R.PlanoPagamento])
def planos(ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    return [as_dict(p) for p in db.scalars(select(PlanoPagamento).where(PlanoPagamento.estabelecimento_id == ctx.estabelecimento_id))]


@router.post("/planos-pagamento", response_model=R.PlanoPagamento, status_code=201)
def criar_plano(dados: PlanoIn, ctx: Contexto = Depends(criar), db: Session = Depends(get_db)):
    return gravar(db, PlanoPagamento(estabelecimento_id=ctx.estabelecimento_id, **dados.model_dump()))


@router.get("/multas", response_model=list[R.MultaRegra])
def multas(ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    return [as_dict(m) for m in db.scalars(select(MultaRegra).where(MultaRegra.estabelecimento_id == ctx.estabelecimento_id))]


@router.post("/multas", response_model=R.MultaRegra, status_code=201)
def criar_multa(dados: MultaIn, ctx: Contexto = Depends(criar), db: Session = Depends(get_db)):
    return gravar(db, MultaRegra(estabelecimento_id=ctx.estabelecimento_id, **dados.model_dump()))


@router.get("/descontos", response_model=list[R.Desconto])
def descontos(ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    return [as_dict(d) for d in db.scalars(select(Desconto).where(Desconto.estabelecimento_id == ctx.estabelecimento_id))]


@router.post("/descontos", response_model=R.Desconto, status_code=201)
def criar_desconto(dados: DescontoIn, ctx: Contexto = Depends(criar), db: Session = Depends(get_db)):
    return gravar(db, Desconto(estabelecimento_id=ctx.estabelecimento_id, **dados.model_dump()))


@router.get("/metodos-pagamento", response_model=list[R.MetodoPagamento])
def metodos(ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    return [as_dict(m) for m in db.scalars(select(MetodoPagamento).where(MetodoPagamento.estabelecimento_id == ctx.estabelecimento_id))]


@router.post("/metodos-pagamento", response_model=R.MetodoPagamento, status_code=201)
def criar_metodo(dados: MetodoIn, ctx: Contexto = Depends(criar), db: Session = Depends(get_db)):
    return gravar(db, MetodoPagamento(estabelecimento_id=ctx.estabelecimento_id, **dados.model_dump()))


# ---------------------------------------------------------------- cobranças
EstadoFiltro = Literal["pago", "divida", "atraso", "pendente", "parcial", "paga"] | None


@router.get("/cobrancas", response_model=R.ListaCobrancas, summary="Quem pagou e quem deve (com filtros)")
def listar_cobrancas(turma_id: int | None = None, servico_id: int | None = None, categoria: str | None = None,
                     estado: EstadoFiltro = None, q: str | None = None, aluno_id: int | None = None,
                     encarregado_id: int | None = None, competencia: date | None = None,
                     ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    linhas = fin.cobrancas_detalhe(db, ctx.estabelecimento_id, turma_id=turma_id, servico_id=servico_id,
                                   categoria=categoria, estado=estado, q=q, aluno_id=aluno_id,
                                   encarregado_id=encarregado_id, competencia=competencia)
    return {"resumo": fin.resumo(linhas), "linhas": linhas}


@router.get("/cobrancas/exportar", responses=R.EXCEL, summary="Mapa de cobranças em Excel ou CSV")
def exportar_cobrancas(formato: Literal["xlsx", "csv"] = "xlsx", turma_id: int | None = None,
                       servico_id: int | None = None, estado: EstadoFiltro = None, q: str | None = None,
                       aluno_id: int | None = None,
                       ctx: Contexto = Depends(exige("propinas", "exportar")), db: Session = Depends(get_db)):
    linhas = fin.cobrancas_detalhe(db, ctx.estabelecimento_id, turma_id=turma_id, servico_id=servico_id,
                                   estado=estado, q=q, aluno_id=aluno_id)
    auditar(db, ctx, "cobranca", None, "export", {"registos": len(linhas), "estado": estado})
    db.commit()
    return tabela(COLUNAS_COBRANCA, linhas, "cobrancas", formato)


@router.get("/cobrancas/devedores", response_model=list[R.Devedor])
def devedores(ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    """Equivalente a vw_devedores: quem tem cobranças vencidas por pagar."""
    return fin.devedores(db, ctx.estabelecimento_id)


@router.get("/cobrancas/configuracao", response_model=R.ConfiguracaoCobranca,
            summary="Propina, preços por classe e multa: o que o mapa de cobrança precisa antes de gerar")
def configuracao(ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    return fin.configuracao_cobranca(db, ctx.estabelecimento_id)


@router.post("/cobrancas/gerar", response_model=R.ResultadoGeracao,
             summary="Gera o mapa de cobrança do mês (idempotente) e aplica as multas em atraso")
def gerar(dados: GerarIn, ctx: Contexto = Depends(criar), db: Session = Depends(get_db)):
    """Grava primeiro a configuração enviada (preço da propina e percentagem da multa) e recusa gerar (422)
    enquanto faltar alguma coisa. A multa fica automática: aplicada agora às cobranças já em atraso e,
    depois, todos os dias pelo agendador."""
    est = ctx.estabelecimento_id
    configura = dados.model_dump(mode="json", exclude={"competencia", "servico_id", "todo_o_ano"}, exclude_none=True)
    if configura and not ctx.pode("propinas", "editar"):
        raise HTTPException(403, "Sem permissão para alterar o plano, os preços, a multa ou os alertas: propinas/editar")
    fin.configurar_cobranca(
        db, est, servico_id=dados.servico_id, valor=dados.valor,
        precos=[(p.classe_id, p.valor) for p in dados.precos or []], multa_percentual=dados.multa_percentual,
        dias_tolerancia=dados.dias_tolerancia, multa_maximo=dados.multa_maximo, prestacoes=dados.prestacoes,
        mes_inicio=dados.mes_inicio, dia_vencimento=dados.dia_vencimento, alertas_dias=dados.alertas_dias)
    if configura:
        auditar(db, ctx, "plano_pagamento", None, "update", {"configuracao_cobranca": configura})
    estado = fin.configuracao_cobranca(db, est)
    if not estado["pronto"]:
        db.rollback()
        raise HTTPException(422, "Configure primeiro: " + "; ".join(estado["pendentes"]))
    res = fin.gerar_ano(db, est, dados.competencia) if dados.todo_o_ano else fin.gerar_cobrancas(db, est, dados.competencia)
    res["multas_aplicadas"] = fin.aplicar_multas(db, est)
    auditar(db, ctx, "cobranca", None, "insert", {"competencia": str(res["competencia"]), "criadas": res["criadas"],
                                                  "todo_o_ano": dados.todo_o_ano})
    db.commit()
    return res


@router.post("/cobrancas", response_model=R.Cobranca, status_code=201, summary="Cobrança avulsa (fardamento, actividade, ...)")
def cobranca_avulsa(dados: CobrancaAvulsaIn, ctx: Contexto = Depends(criar), db: Session = Depends(get_db)):
    est = ctx.estabelecimento_id
    ano = ano_activo(db, est)
    m = db.scalar(select(Matricula).where(Matricula.aluno_id == dados.aluno_id, Matricula.ano_id == ano.id,
                                          Matricula.estado == "activa"))
    if not m or m.estabelecimento_id != est:
        raise HTTPException(422, "O aluno não tem matrícula activa")
    s = obter(db, Servico, dados.servico_id, est, "Serviço")
    base = dados.valor_base
    if base is None:
        base = fin.preco(db, s.id, ano.id, db.get(Turma, m.turma_id).classe_id)
        if base is None:
            raise HTTPException(422, f"O serviço {s.nome} não tem preço definido para o ano activo")
    competencia = dados.competencia.replace(day=1)
    c = Cobranca(estabelecimento_id=est, matricula_id=m.id, aluno_id=m.aluno_id,
                 encarregado_id=fin.encarregado_financeiro(db, m.aluno_id), servico_id=s.id, competencia=competencia,
                 descricao=dados.descricao or s.nome, valor_base=Decimal(base),
                 vencimento=dados.vencimento or competencia.replace(day=10))
    res = gravar(db, c, "Já existe esta cobrança para o mês indicado")
    auditar(db, ctx, "cobranca", c.id, "insert", res)
    db.commit()
    return res


@router.post("/cobrancas/{cid}/anular", response_model=R.Cobranca)
def anular(cid: int, ctx: Contexto = Depends(exige("propinas", "apagar")), db: Session = Depends(get_db)):
    c = obter(db, Cobranca, cid, ctx.estabelecimento_id, "Cobrança")
    if fin.valor_em_aberto(db, c) < c.valor_base - c.valor_desconto + c.valor_multa:
        raise HTTPException(409, "A cobrança já tem pagamentos; anule primeiro o pagamento")
    c.estado = "anulada"
    auditar(db, ctx, "cobranca", cid, "update", {"estado": "anulada"})
    db.commit()
    return as_dict(c)


@router.post("/cobrancas/aplicar-multas", response_model=R.MultasAplicadas)
def multas_atraso(ctx: Contexto = Depends(editar), db: Session = Depends(get_db)):
    return {"cobrancas_actualizadas": fin.aplicar_multas(db, ctx.estabelecimento_id)}


@router.post("/cobrancas/lembretes", response_model=R.LembretesEnviados, summary="Enviar lembrete aos devedores")
def lembretes(dados: LembreteIn, ctx: Contexto = Depends(exige("comunicacao", "criar")), db: Session = Depends(get_db)):
    linhas = fin.cobrancas_detalhe(db, ctx.estabelecimento_id, turma_id=dados.turma_id, servico_id=dados.servico_id,
                                   categoria=dados.categoria, q=dados.q, estado="divida")
    if dados.cobranca_ids:
        linhas = [x for x in linhas if x["cobranca_id"] in set(dados.cobranca_ids)]
    n = enfileirar_lembretes(db, ctx.estabelecimento_id, linhas, list(dados.canais))
    return {"mensagens": n, "encarregados": len({x["encarregado_id"] for x in linhas if x["encarregado_id"]})}


# ---------------------------------------------------------------- pagamentos
@router.get("/pagamentos", response_model=list[R.PagamentoLista])
def listar_pagamentos(encarregado_id: int | None = None, desde: date | None = None, ate: date | None = None,
                      estado: str | None = None, ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    stmt = (select(Pagamento, Encarregado.nome_completo, MetodoPagamento.nome)
            .outerjoin(Encarregado, Encarregado.id == Pagamento.encarregado_id)
            .join(MetodoPagamento, MetodoPagamento.id == Pagamento.metodo_id)
            .where(Pagamento.estabelecimento_id == ctx.estabelecimento_id))
    if encarregado_id:
        stmt = stmt.where(Pagamento.encarregado_id == encarregado_id)
    if desde:
        stmt = stmt.where(Pagamento.data_pagamento >= desde)
    if ate:
        stmt = stmt.where(Pagamento.data_pagamento <= ate)
    if estado:
        stmt = stmt.where(Pagamento.estado == estado)
    pags = db.execute(stmt.order_by(Pagamento.data_pagamento.desc(), Pagamento.id.desc()).limit(1000)).all()
    aloc = {}
    for a in db.scalars(select(PagamentoAlocacao).where(PagamentoAlocacao.pagamento_id.in_([p.id for p, *_ in pags]))):
        aloc.setdefault(a.pagamento_id, []).append({"cobranca_id": a.cobranca_id, "valor": float(a.valor)})
    return [{**as_dict(p), "encarregado": e, "metodo": m, "alocacoes": aloc.get(p.id, [])} for p, e, m in pags]


@router.post("/pagamentos", response_model=R.PagamentoComAlocacoes, status_code=201, summary="Regista um pagamento e salda as cobranças")
def pagar(dados: PagamentoIn, ctx: Contexto = Depends(criar), db: Session = Depends(get_db)):
    if dados.encarregado_id:
        obter(db, Encarregado, dados.encarregado_id, ctx.estabelecimento_id, "Encarregado")
    pg = fin.registar_pagamento(
        db, ctx.estabelecimento_id, ctx.id, metodo_id=dados.metodo_id, valor=dados.valor,
        encarregado_id=dados.encarregado_id, cobranca_ids=dados.cobranca_ids,
        alocacoes=[(a.cobranca_id, a.valor) for a in dados.alocacoes] if dados.alocacoes else None,
        data_pagamento=dados.data_pagamento, referencia=dados.referencia, comprovativo_url=dados.comprovativo_url,
        estado=dados.estado)
    auditar(db, ctx, "pagamento", pg.id, "insert", as_dict(pg))
    db.commit()
    aloc = db.scalars(select(PagamentoAlocacao).where(PagamentoAlocacao.pagamento_id == pg.id))
    return {**as_dict(pg), "alocacoes": [{"cobranca_id": a.cobranca_id, "valor": float(a.valor)} for a in aloc]}


def _decidir(pid: int, novo: str, ctx: Contexto, db: Session):
    pg = obter(db, Pagamento, pid, ctx.estabelecimento_id, "Pagamento")
    fin.mudar_estado_pagamento(db, pg, novo, tratado_por=ctx.id)
    auditar(db, ctx, "pagamento", pid, "update", {"estado": novo})
    db.commit()
    return as_dict(pg)


@router.post("/pagamentos/{pid}/confirmar", response_model=R.Pagamento, summary="Confirma um pagamento pendente (ex.: comprovativo verificado)")
def confirmar_pagamento(pid: int, ctx: Contexto = Depends(editar), db: Session = Depends(get_db)):
    return _decidir(pid, "confirmado", ctx, db)


@router.post("/pagamentos/{pid}/rejeitar", response_model=R.Pagamento, summary="Rejeita um pagamento pendente; as cobranças voltam a ficar em dívida")
def rejeitar_pagamento(pid: int, ctx: Contexto = Depends(editar), db: Session = Depends(get_db)):
    return _decidir(pid, "rejeitado", ctx, db)
