"""Pedidos do encarregado, do lado da escola: lista, detalhe e o tratamento (analisar, pedir informação à família,
encaminhar, aprovar — executa o efeito —, rejeitar e concluir)."""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import respostas as R
from app.common import obter
from app.database import get_db
from app.models import Aluno, Pedido
from app.schemas import ConcluirIn, DecisaoIn, EncaminharIn, MensagemPedidoIn
from app.security import Contexto, auditar, exige
from app.services import pedidos as svc
from app.services import secretaria

router = APIRouter(prefix="/api", tags=["Pedidos do encarregado"])
# Quem trata cada serviço: a permissão de editar no módulo correspondente.
MODULO_DO_SERVICO = {"coordenacao": "pedagogico", "tesouraria": "propinas", "secretaria": "cadastro", "direccao": "painel",
                     "saude": "saude"}


def _pode_tratar(ctx: Contexto, p: Pedido) -> None:
    modulo = MODULO_DO_SERVICO[p.servico]
    if not ctx.pode(modulo, "editar"):
        raise HTTPException(403, f"Sem permissão: {modulo}/editar (pedido da {p.servico})")


def _obter(db: Session, ctx: Contexto, pid: int) -> Pedido:
    p = obter(db, Pedido, pid, ctx.estabelecimento_id, "Pedido")
    _pode_tratar(ctx, p)
    return p


def _fim(db: Session, ctx: Contexto, p: Pedido, passo: str) -> dict:
    auditar(db, ctx, "pedido", p.id, "update", {"passo": passo, "estado": p.estado, "servico": p.servico})
    db.commit()
    return svc.com_nomes(db, p)


@router.get("/pedidos", response_model=list[R.PedidoComNomes],
            summary="Pedidos dos encarregados (estado=abertos: recebidos, em análise e a aguardar informação)")
def listar(estado: str | None = None, tipo: str | None = None, aluno_id: int | None = None, servico: str | None = None,
           ctx: Contexto = Depends(exige("cadastro")), db: Session = Depends(get_db)):
    stmt = select(Pedido).where(Pedido.estabelecimento_id == ctx.estabelecimento_id)
    if estado == "abertos":
        stmt = stmt.where(Pedido.estado.in_(svc.ABERTOS))
    elif estado:
        stmt = stmt.where(Pedido.estado == estado)
    for coluna, valor in ((Pedido.tipo, tipo), (Pedido.aluno_id, aluno_id), (Pedido.servico, servico)):
        if valor:
            stmt = stmt.where(coluna == valor)
    return [svc.com_nomes(db, p) for p in db.scalars(stmt.order_by(Pedido.criado_em.desc()).limit(500))]


@router.get("/pedidos/{pid}", response_model=R.PedidoComNomes)
def ver(pid: int, ctx: Contexto = Depends(exige("cadastro")), db: Session = Depends(get_db)):
    return svc.com_nomes(db, obter(db, Pedido, pid, ctx.estabelecimento_id, "Pedido"))


@router.post("/pedidos/{pid}/analisar", response_model=R.PedidoComNomes, summary="Passa o pedido a «em análise» (a família é avisada)")
def analisar(pid: int, ctx: Contexto = Depends(exige("cadastro")), db: Session = Depends(get_db)):
    p = _obter(db, ctx, pid)
    svc.analisar(db, p, ctx.utilizador)
    return _fim(db, ctx, p, "analisar")


@router.post("/pedidos/{pid}/pedir-informacao", response_model=R.PedidoComNomes,
             summary="Pede informação à família; o pedido fica a aguardar a resposta no portal")
def pedir_informacao(pid: int, dados: MensagemPedidoIn, ctx: Contexto = Depends(exige("cadastro")),
                     db: Session = Depends(get_db)):
    p = _obter(db, ctx, pid)
    svc.pedir_informacao(db, p, ctx.utilizador, dados.mensagem)
    return _fim(db, ctx, p, "pedir_informacao")


@router.post("/pedidos/{pid}/encaminhar", response_model=R.PedidoComNomes,
             summary="Encaminha para outro serviço (secretaria, coordenação, tesouraria, Direcção)")
def encaminhar(pid: int, dados: EncaminharIn, ctx: Contexto = Depends(exige("cadastro")), db: Session = Depends(get_db)):
    p = _obter(db, ctx, pid)
    svc.encaminhar(db, p, ctx.utilizador, dados.servico, dados.nota)
    return _fim(db, ctx, p, "encaminhar")


@router.post("/pedidos/{pid}/aprovar", response_model=R.PedidoComNomes,
             summary="Aprova e executa: matrícula (com a ficha confirmada), transferência, declaração, justificação…")
def aprovar(pid: int, dados: DecisaoIn, ctx: Contexto = Depends(exige("cadastro")), db: Session = Depends(get_db)):
    p = _obter(db, ctx, pid)
    svc.decidir(db, p, True, dados.resposta, dados.turma_id, ctx.id)
    auditar(db, ctx, "pedido", pid, "update", {"estado": "aprovado", "resultado": p.resultado})
    db.commit()
    return svc.com_nomes(db, p)


@router.post("/pedidos/{pid}/rejeitar", response_model=R.PedidoComNomes, summary="Recusa o pedido (com o motivo) e avisa o encarregado")
def rejeitar(pid: int, dados: DecisaoIn, ctx: Contexto = Depends(exige("cadastro")), db: Session = Depends(get_db)):
    p = _obter(db, ctx, pid)
    svc.decidir(db, p, False, dados.resposta, None, ctx.id)
    auditar(db, ctx, "pedido", pid, "update", {"estado": "rejeitado"})
    db.commit()
    return svc.com_nomes(db, p)


@router.post("/pedidos/{pid}/concluir", response_model=R.PedidoComNomes,
             summary="Responde e conclui (informação, financeiro, outro) ou fecha um aprovado (ex.: declaração entregue)")
def concluir(pid: int, dados: ConcluirIn, ctx: Contexto = Depends(exige("cadastro")), db: Session = Depends(get_db)):
    p = _obter(db, ctx, pid)
    svc.concluir(db, p, ctx.utilizador, dados.resposta)
    return _fim(db, ctx, p, "concluir")


@router.get("/pedidos/{pid}/declaracao.pdf", responses=R.PDF, summary="A declaração do pedido, para imprimir e assinar")
def declaracao(pid: int, ctx: Contexto = Depends(exige("cadastro")), db: Session = Depends(get_db)):
    p = obter(db, Pedido, pid, ctx.estabelecimento_id, "Pedido")
    if p.tipo != "declaracao" or p.estado not in ("aprovado", "concluido"):
        raise HTTPException(409, "A declaração só existe depois de o pedido ser aprovado")
    md = secretaria.modelo(db, p.estabelecimento_id, p.dados.get("modelo") or "matricula")
    pdf = secretaria.declaracao(db, db.get(Aluno, p.aluno_id), md, p.dados.get("finalidade"))
    return Response(pdf, media_type="application/pdf")
