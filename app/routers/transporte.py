"""Transporte escolar: viaturas, rotas com paragens, inscrição dos alunos (lotação e cobrança) e lista de passageiros."""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import crud
from app import respostas as R
from app.common import ano_activo, aplicar, as_dict, obter
from app.database import get_db
from app.models import (
    AdesaoServico, Aluno, AlunoEncarregado, AlunoTransporte, Encarregado, Matricula, ParagemTransporte,
    RotaTransporte, Servico, Turma, Viatura,
)
from app.schemas import InscricaoTransporteIn, InscricaoTransporteUpd, ParagemIn, RotaIn, ViaturaIn
from app.security import Contexto, auditar, exige

router = APIRouter(prefix="/api/transporte", tags=["Transporte"])
ver, criar, editar = exige("transporte"), exige("transporte", "criar"), exige("transporte", "editar")


def _gravar(db: Session, obj, msg: str):
    try:
        db.add(obj)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, msg)
    return as_dict(obj)


def inscritos(db: Session, rota_id: int) -> int:
    return db.scalar(select(func.count()).select_from(AlunoTransporte)
                     .where(AlunoTransporte.rota_id == rota_id, AlunoTransporte.estado == "activa"))


def _rota_completa(db: Session, r: RotaTransporte) -> dict:
    v = db.get(Viatura, r.viatura_id) if r.viatura_id else None
    n = inscritos(db, r.id)
    paragens = db.scalars(select(ParagemTransporte).where(ParagemTransporte.rota_id == r.id)
                          .order_by(ParagemTransporte.ordem)).all()
    return {**as_dict(r), "viatura": v.matricula if v else None, "motorista": v.motorista if v else None,
            "telefone_motorista": v.telefone_motorista if v else None, "lugares": v.lugares if v else None,
            "inscritos": n, "lugares_livres": max(0, v.lugares - n) if v else None,
            "paragens": [as_dict(p) for p in paragens]}


# ---------------------------------------------------------------- viaturas
@router.get("/viaturas", response_model=list[R.Viatura])
def viaturas(ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    return [as_dict(v) for v in db.scalars(select(Viatura).where(Viatura.estabelecimento_id == ctx.estabelecimento_id)
                                           .order_by(Viatura.matricula))]


@router.post("/viaturas", response_model=R.Viatura, status_code=201)
def criar_viatura(dados: ViaturaIn, ctx: Contexto = Depends(criar), db: Session = Depends(get_db)):
    return _gravar(db, Viatura(estabelecimento_id=ctx.estabelecimento_id, **dados.model_dump()),
                   "Já existe uma viatura com esta matrícula")


# ---------------------------------------------------------------- rotas e paragens
@router.get("/rotas", response_model=list[R.RotaCompleta], summary="Rotas com viatura, lotação e paragens")
def rotas(ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    return [_rota_completa(db, r) for r in db.scalars(select(RotaTransporte).where(
        RotaTransporte.estabelecimento_id == ctx.estabelecimento_id).order_by(RotaTransporte.nome))]


def _validar_rota(db: Session, est: int, viatura_id: int | None, servico_id: int | None) -> None:
    if viatura_id:
        obter(db, Viatura, viatura_id, est, "Viatura")
    if servico_id:
        obter(db, Servico, servico_id, est, "Serviço")


@router.post("/rotas", response_model=R.RotaCompleta, status_code=201)
def criar_rota(dados: RotaIn, ctx: Contexto = Depends(criar), db: Session = Depends(get_db)):
    est = ctx.estabelecimento_id
    _validar_rota(db, est, dados.viatura_id, dados.servico_id)
    servico_id = dados.servico_id or db.scalar(select(Servico.id).where(
        Servico.estabelecimento_id == est, Servico.categoria == "transporte", Servico.activo).limit(1))
    r = RotaTransporte(estabelecimento_id=est, **dados.model_dump(exclude={"paragens", "servico_id"}),
                       servico_id=servico_id)
    _gravar(db, r, "Já existe uma rota com este nome")
    for p in dados.paragens:
        db.add(ParagemTransporte(rota_id=r.id, **p.model_dump()))
    db.commit()
    return _rota_completa(db, r)


@router.post("/rotas/{rid}/paragens", response_model=R.ParagemTransporte, status_code=201)
def criar_paragem(rid: int, dados: ParagemIn, ctx: Contexto = Depends(criar), db: Session = Depends(get_db)):
    obter(db, RotaTransporte, rid, ctx.estabelecimento_id, "Rota")
    return _gravar(db, ParagemTransporte(rota_id=rid, **dados.model_dump()), "Paragem duplicada")


@router.get("/rotas/{rid}/alunos", response_model=list[R.PassageiroTransporte],
            summary="Lista de passageiros da rota, por paragem, com o contacto do encarregado")
def passageiros(rid: int, ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    obter(db, RotaTransporte, rid, ctx.estabelecimento_id, "Rota")
    ano = ano_activo(db, ctx.estabelecimento_id, obrigatorio=False)
    rows = db.execute(select(AlunoTransporte, Aluno, ParagemTransporte)
                      .join(Aluno, Aluno.id == AlunoTransporte.aluno_id)
                      .outerjoin(ParagemTransporte, ParagemTransporte.id == AlunoTransporte.paragem_id)
                      .where(AlunoTransporte.rota_id == rid, AlunoTransporte.estado == "activa")
                      .order_by(ParagemTransporte.ordem, Aluno.nome_completo)).all()
    out = []
    for i, a, p in rows:
        turma = ano and db.scalar(select(Turma.designacao).join(Matricula, Matricula.turma_id == Turma.id).where(
            Matricula.aluno_id == a.id, Matricula.ano_id == ano.id, Matricula.estado == "activa"))
        ee = db.execute(select(Encarregado.nome_completo, Encarregado.telefone)
                        .join(AlunoEncarregado, AlunoEncarregado.encarregado_id == Encarregado.id)
                        .where(AlunoEncarregado.aluno_id == a.id).order_by(AlunoEncarregado.principal.desc())).first()
        out.append({"inscricao_id": i.id, "aluno_id": a.id, "aluno": a.nome_completo, "turma": turma or None,
                    "paragem": p.nome if p else None, "ordem": p.ordem if p else None, "sentido": i.sentido,
                    "encarregado": ee[0] if ee else None, "telefone": ee[1] if ee else None,
                    "necessidade_especial": bool((a.necessidades_especiais or "").strip())})
    return out


# ---------------------------------------------------------------- inscrições
@router.get("/inscricoes", response_model=list[R.AlunoTransporte])
def inscricoes(rota_id: int | None = None, aluno_id: int | None = None, estado: str | None = None,
               ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    stmt = select(AlunoTransporte).where(AlunoTransporte.estabelecimento_id == ctx.estabelecimento_id)
    for coluna, valor in ((AlunoTransporte.rota_id, rota_id), (AlunoTransporte.aluno_id, aluno_id),
                          (AlunoTransporte.estado, estado)):
        if valor is not None:
            stmt = stmt.where(coluna == valor)
    return [as_dict(i) for i in db.scalars(stmt.order_by(AlunoTransporte.id.desc()))]


def _paragem_da_rota(db: Session, paragem_id: int | None, rota_id: int) -> None:
    if paragem_id is not None:
        p = db.get(ParagemTransporte, paragem_id)
        if p is None or p.rota_id != rota_id:
            raise HTTPException(422, "A paragem não pertence a esta rota")


@router.post("/inscricoes", response_model=R.AlunoTransporte, status_code=201,
             summary="Inscreve o aluno numa rota (recusa se a viatura estiver cheia) e, com cobrar, na cobrança do serviço")
def inscrever(dados: InscricaoTransporteIn, ctx: Contexto = Depends(criar), db: Session = Depends(get_db)):
    est = ctx.estabelecimento_id
    obter(db, Aluno, dados.aluno_id, est, "Aluno")
    rota = obter(db, RotaTransporte, dados.rota_id, est, "Rota")
    _paragem_da_rota(db, dados.paragem_id, rota.id)
    if db.scalar(select(AlunoTransporte.id).where(AlunoTransporte.aluno_id == dados.aluno_id,
                                                  AlunoTransporte.estado == "activa")):
        raise HTTPException(409, "O aluno já está inscrito numa rota: cancele essa inscrição primeiro")
    v = db.get(Viatura, rota.viatura_id) if rota.viatura_id else None
    if v and inscritos(db, rota.id) >= v.lugares:
        raise HTTPException(409, f"A viatura {v.matricula} da {rota.nome} está cheia ({v.lugares} lugares)")
    inicio = dados.data_inicio or date.today()
    adesao_id = None
    if dados.cobrar and rota.servico_id:
        ano = ano_activo(db, est)
        m = db.scalar(select(Matricula).where(Matricula.aluno_id == dados.aluno_id, Matricula.ano_id == ano.id,
                                              Matricula.estado == "activa"))
        if m is None:
            raise HTTPException(422, "O aluno não tem matrícula activa: não é possível cobrar o transporte")
        adesao = db.scalar(select(AdesaoServico).where(AdesaoServico.matricula_id == m.id,
                                                       AdesaoServico.servico_id == rota.servico_id,
                                                       AdesaoServico.estado == "activa"))
        if adesao is None:
            adesao = AdesaoServico(matricula_id=m.id, servico_id=rota.servico_id, data_inicio=inicio)
            db.add(adesao)
            db.flush()
        adesao_id = adesao.id
    i = AlunoTransporte(estabelecimento_id=est, aluno_id=dados.aluno_id, rota_id=rota.id, paragem_id=dados.paragem_id,
                        sentido=dados.sentido, data_inicio=inicio, adesao_id=adesao_id)
    db.add(i)
    db.flush()
    auditar(db, ctx, "aluno_transporte", i.id, "insert", as_dict(i))
    db.commit()
    return as_dict(i)


@router.patch("/inscricoes/{iid}", response_model=R.AlunoTransporte,
              summary="Muda a paragem ou o sentido; estado cancelada/suspensa também pára a cobrança")
def editar_inscricao(iid: int, dados: InscricaoTransporteUpd, ctx: Contexto = Depends(editar), db: Session = Depends(get_db)):
    i = obter(db, AlunoTransporte, iid, ctx.estabelecimento_id, "Inscrição")
    mudancas = dados.model_dump(exclude_unset=True)
    _paragem_da_rota(db, mudancas.get("paragem_id"), i.rota_id)
    aplicar(i, mudancas)
    if mudancas.get("estado") in ("cancelada", "suspensa"):
        i.data_fim = date.today() if mudancas["estado"] == "cancelada" else i.data_fim
        if i.adesao_id:
            adesao = db.get(AdesaoServico, i.adesao_id)
            adesao.estado = mudancas["estado"]
            if mudancas["estado"] == "cancelada":
                adesao.data_fim = date.today()
    auditar(db, ctx, "aluno_transporte", iid, "update", mudancas)
    db.commit()
    return as_dict(i)


# ---------------------------------------------------------------- CRUD completo (ver, editar, apagar)
crud.registar(router, "/viaturas", Viatura, nome="Viatura", modulo="transporte", resposta=R.Viatura, entrada=ViaturaIn)
crud.registar(router, "/rotas", RotaTransporte, nome="Rota", modulo="transporte", resposta=R.RotaTransporte,
              entrada=RotaIn, excluir=("paragens",))
crud.registar(router, "/paragens", ParagemTransporte, nome="Paragem", modulo="transporte",
              resposta=R.ParagemTransporte, entrada=ParagemIn)
crud.registar(router, "/inscricoes", AlunoTransporte, nome="Inscrição", modulo="transporte", resposta=R.AlunoTransporte,
              ops=("ver",))
