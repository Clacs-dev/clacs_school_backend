"""Pedidos do encarregado, do lado da escola: lista, detalhe, aprovar (executa o efeito) e rejeitar."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import respostas as R
from app.common import obter
from app.database import get_db
from app.models import Pedido
from app.schemas import DecisaoIn
from app.security import Contexto, auditar, exige
from app.services import pedidos as svc

router = APIRouter(prefix="/api", tags=["Pedidos do encarregado"])


def _pode_decidir(ctx: Contexto, p: Pedido) -> None:
    modulo = "pedagogico" if p.tipo == "justificacao_falta" else "cadastro"
    if not ctx.pode(modulo, "editar"):
        raise HTTPException(403, f"Sem permissão: {modulo}/editar")


@router.get("/pedidos", response_model=list[R.PedidoComNomes], summary="Pedidos dos encarregados (estado=pendente: por decidir)")
def listar(estado: str | None = None, tipo: str | None = None, aluno_id: int | None = None,
           ctx: Contexto = Depends(exige("cadastro")), db: Session = Depends(get_db)):
    stmt = select(Pedido).where(Pedido.estabelecimento_id == ctx.estabelecimento_id)
    if estado:
        stmt = stmt.where(Pedido.estado == estado)
    if tipo:
        stmt = stmt.where(Pedido.tipo == tipo)
    if aluno_id:
        stmt = stmt.where(Pedido.aluno_id == aluno_id)
    return [svc.com_nomes(db, p) for p in db.scalars(stmt.order_by(Pedido.criado_em.desc()).limit(500))]


@router.get("/pedidos/{pid}", response_model=R.PedidoComNomes)
def ver(pid: int, ctx: Contexto = Depends(exige("cadastro")), db: Session = Depends(get_db)):
    return svc.com_nomes(db, obter(db, Pedido, pid, ctx.estabelecimento_id, "Pedido"))


@router.post("/pedidos/{pid}/aprovar", response_model=R.PedidoComNomes,
             summary="Aprova e executa: matrícula, transferência, declaração ou justificação; avisa o encarregado")
def aprovar(pid: int, dados: DecisaoIn, ctx: Contexto = Depends(exige("cadastro")), db: Session = Depends(get_db)):
    p = obter(db, Pedido, pid, ctx.estabelecimento_id, "Pedido")
    _pode_decidir(ctx, p)
    svc.decidir(db, p, True, dados.resposta, dados.turma_id, ctx.id)
    auditar(db, ctx, "pedido", pid, "update", {"estado": "aprovado", "resultado": p.resultado})
    db.commit()
    return svc.com_nomes(db, p)


@router.post("/pedidos/{pid}/rejeitar", response_model=R.PedidoComNomes, summary="Recusa o pedido (com o motivo) e avisa o encarregado")
def rejeitar(pid: int, dados: DecisaoIn, ctx: Contexto = Depends(exige("cadastro")), db: Session = Depends(get_db)):
    p = obter(db, Pedido, pid, ctx.estabelecimento_id, "Pedido")
    _pode_decidir(ctx, p)
    svc.decidir(db, p, False, dados.resposta, None, ctx.id)
    auditar(db, ctx, "pedido", pid, "update", {"estado": "rejeitado"})
    db.commit()
    return svc.com_nomes(db, p)
