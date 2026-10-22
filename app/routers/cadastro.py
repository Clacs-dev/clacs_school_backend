from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import crud
from app import respostas as R
from app.common import ano_activo, aplicar, as_dict, obter
from app.database import get_db
from app.models import (
    AdesaoServico, Aluno, AlunoEncarregado, AnoLectivo, AtribuicaoDocente, Classe, ClasseDisciplina, Desconto, Disciplina,
    Funcionario, DocumentoAluno, Encarregado, Matricula,
    MatriculaDesconto, MatriculaHistorico, Ocorrencia, PeriodoLectivo, PreferenciaContacto, Servico, Turma,
)
from app.schemas import (
    AlunoIn, AlunoUpd, DocumentoIn, EncarregadoIn, EncarregadoUpd, LigacaoIn, MatriculaDescontoIn, MatriculaIn,
    MudancaTurmaIn, PreferenciaIn,
)
from app.security import Contexto, auditar, exige, turmas_permitidas
from app.services.alertas import comportamento_actual
from app.services.financeiro import cobrancas_detalhe
from app.services import turmas as ocupacao_turmas
from app.services.pedagogico import media_geral, media_ponderada, total_faltas

router = APIRouter(prefix="/api", tags=["Cadastro e matrículas"])
ver, criar, editar = exige("cadastro"), exige("cadastro", "criar"), exige("cadastro", "editar")


def _numero_processo(db: Session, est: int) -> str:
    ano = date.today().year
    n = db.scalar(select(func.count()).select_from(Aluno).where(Aluno.estabelecimento_id == est)) + 1
    while db.scalar(select(Aluno.id).where(Aluno.estabelecimento_id == est, Aluno.numero_processo == f"{ano}{n:04d}")):
        n += 1
    return f"{ano}{n:04d}"


def _turma_do_ano(db: Session, turma_id: int | None, est: int, ano_id: int, classe_id: int | None = None) -> Turma:
    """A turma pedida (409 se cheia, com a próxima turma com vaga) ou, só com a classe, a 1.ª turma com vaga."""
    if turma_id is None:
        obter(db, Classe, classe_id, nome="Classe")
        return ocupacao_turmas.escolher(db, est, ano_id, classe_id)
    t = obter(db, Turma, turma_id, est, "Turma")
    if t.ano_id != ano_id:
        raise HTTPException(422, "A turma não pertence ao ano lectivo activo")
    ocupacao_turmas.garantir_vaga(db, t)
    return t


def _matricula_activa(db: Session, aluno_id: int, est: int) -> Matricula | None:
    ano = ano_activo(db, est, obrigatorio=False)
    if not ano:
        return None
    return db.scalar(select(Matricula).where(Matricula.aluno_id == aluno_id, Matricula.ano_id == ano.id,
                                             Matricula.estado == "activa"))


def disciplinas_do_aluno(db: Session, aluno_id: int, est: int) -> list[dict]:
    """As disciplinas do plano curricular da classe da turma actual do aluno, com o professor da turma."""
    m = _matricula_activa(db, aluno_id, est)
    if m is None:
        return []
    t = db.get(Turma, m.turma_id)
    rows = db.execute(
        select(Disciplina, ClasseDisciplina.carga_horaria, Funcionario.id, Funcionario.nome_completo)
        .join(ClasseDisciplina, ClasseDisciplina.disciplina_id == Disciplina.id)
        .outerjoin(AtribuicaoDocente, (AtribuicaoDocente.disciplina_id == Disciplina.id)
                   & (AtribuicaoDocente.turma_id == t.id))
        .outerjoin(Funcionario, Funcionario.id == AtribuicaoDocente.funcionario_id)
        .where(ClasseDisciplina.classe_id == t.classe_id).order_by(Disciplina.nome))
    return [{**as_dict(d), "carga_horaria": ch, "professor_id": pid, "professor": prof} for d, ch, pid, prof in rows]


# ---------------------------------------------------------------- alunos
@router.get("/alunos", response_model=list[R.AlunoLista])
def listar_alunos(q: str | None = None, turma_id: int | None = None, estado: str | None = None,
                  limit: int = 500, offset: int = 0, ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    """Nº de processo, nome, turma, encarregado, estado, média de cada trimestre e o sinal de necessidade especial."""
    return lista_alunos(db, ctx.estabelecimento_id, q, turma_id, estado, limit, offset)


def lista_alunos(db: Session, est: int, q: str | None = None, turma_id: int | None = None, estado: str | None = None,
                 limit: int = 500, offset: int = 0, turmas: set[int] | None = None) -> list[dict]:
    """turmas = só alunos destas turmas (professores, no pedagógico)."""
    ano = ano_activo(db, est, obrigatorio=False)
    mat = (select(Matricula.aluno_id, Matricula.turma_id, Turma.designacao.label("turma"))
           .join(Turma, Turma.id == Matricula.turma_id)
           .where(Matricula.ano_id == (ano.id if ano else -1), Matricula.estado == "activa").subquery())
    ee = (select(AlunoEncarregado.aluno_id, func.min(Encarregado.nome_completo).label("ee"),
                 func.min(Encarregado.telefone).label("tel"))
          .join(Encarregado, Encarregado.id == AlunoEncarregado.encarregado_id)
          .where(AlunoEncarregado.principal).group_by(AlunoEncarregado.aluno_id).subquery())
    stmt = (select(Aluno, mat.c.turma_id, mat.c.turma, ee.c.ee, ee.c.tel)
            .outerjoin(mat, mat.c.aluno_id == Aluno.id).outerjoin(ee, ee.c.aluno_id == Aluno.id)
            .where(Aluno.estabelecimento_id == est))
    if q:
        stmt = stmt.where(Aluno.nome_completo.ilike(f"%{q.strip()}%") | (Aluno.numero_processo == q.strip()))
    if turma_id:
        stmt = stmt.where(mat.c.turma_id == turma_id)
    if turmas is not None:
        stmt = stmt.where(mat.c.turma_id.in_(turmas))
    if estado:
        stmt = stmt.where(Aluno.estado == estado)
    stmt = stmt.order_by(Aluno.nome_completo).limit(min(limit, 2000)).offset(offset)
    periodos = db.scalars(select(PeriodoLectivo).where(PeriodoLectivo.ano_id == ano.id)
                          .order_by(PeriodoLectivo.ordem)).all() if ano else []
    return [{**as_dict(a), "turma_id": tid, "turma": t, "encarregado": e, "telefone": tel,
             "ano_lectivo": ano.designacao if (ano and tid) else None,
             "necessidade_especial": bool((a.necessidades_especiais or "").strip()),
             "avaliacoes": [{"periodo_id": p.id, "periodo": p.designacao,
                             "media": media_ponderada(db, a.id, tid, None, p.id) if tid else None} for p in periodos]}
            for a, tid, t, e, tel in db.execute(stmt)]


@router.get("/pedagogico/alunos", response_model=list[R.AlunoLista], tags=["Pedagógico"],
            summary="Lista de alunos no pedagógico (professores: só as suas turmas)")
def alunos_pedagogico(turma_id: int | None = None, q: str | None = None,
                      ctx: Contexto = Depends(exige("pedagogico")), db: Session = Depends(get_db)):
    permitidas = turmas_permitidas(db, ctx)
    if turma_id and permitidas is not None and turma_id not in permitidas:
        raise HTTPException(403, "Sem acesso a esta turma")
    return lista_alunos(db, ctx.estabelecimento_id, q, turma_id, "activo", turmas=permitidas)


@router.post("/alunos", response_model=R.Aluno, status_code=201)
def criar_aluno(dados: AlunoIn, ctx: Contexto = Depends(criar), db: Session = Depends(get_db)):
    est = ctx.estabelecimento_id
    campos = dados.model_dump(exclude={"encarregado_id", "parentesco", "turma_id", "classe_id"}, exclude_none=True)
    campos.setdefault("numero_processo", _numero_processo(db, est))
    if db.scalar(select(Aluno.id).where(Aluno.estabelecimento_id == est,
                                        Aluno.numero_processo == campos["numero_processo"])):
        raise HTTPException(409, "Já existe um aluno com este número de processo")
    turma = None
    if dados.turma_id or dados.classe_id:
        ano = ano_activo(db, est)
        turma = _turma_do_ano(db, dados.turma_id, est, ano.id, dados.classe_id)
    if dados.encarregado_id:
        obter(db, Encarregado, dados.encarregado_id, est, "Encarregado")

    a = Aluno(estabelecimento_id=est, **campos)
    db.add(a)
    db.flush()
    if dados.encarregado_id:
        db.add(AlunoEncarregado(aluno_id=a.id, encarregado_id=dados.encarregado_id, parentesco=dados.parentesco,
                                principal=True, responsavel_financeiro=True))
    if turma:
        db.add(Matricula(estabelecimento_id=est, aluno_id=a.id, ano_id=turma.ano_id, turma_id=turma.id,
                         tipo="nova", registado_por=ctx.id))
    auditar(db, ctx, "aluno", a.id, "insert", as_dict(a))
    db.commit()
    return as_dict(a)


@router.get("/alunos/{aid}", response_model=R.AlunoDetalhe)
def ver_aluno(aid: int, ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    a = obter(db, Aluno, aid, ctx.estabelecimento_id, "Aluno")
    ees = db.execute(select(Encarregado, AlunoEncarregado).join(AlunoEncarregado, AlunoEncarregado.encarregado_id == Encarregado.id)
                     .where(AlunoEncarregado.aluno_id == aid))
    mats = db.execute(select(Matricula, Turma.designacao, AnoLectivo.designacao)
                      .join(Turma, Turma.id == Matricula.turma_id).join(AnoLectivo, AnoLectivo.id == Matricula.ano_id)
                      .where(Matricula.aluno_id == aid).order_by(AnoLectivo.data_inicio.desc()))
    docs = db.scalars(select(DocumentoAluno).where(DocumentoAluno.aluno_id == aid))
    return {**as_dict(a), "necessidade_especial": bool((a.necessidades_especiais or "").strip()),
            "encarregados": [{**as_dict(e), **{k: v for k, v in as_dict(l).items() if k not in ("aluno_id",)}}
                             for e, l in ees],
            "matriculas": [{**as_dict(m), "turma": t, "ano_lectivo": an} for m, t, an in mats],
            "documentos": [as_dict(d) for d in docs],
            "disciplinas": disciplinas_do_aluno(db, aid, ctx.estabelecimento_id)}


@router.get("/alunos/{aid}/disciplinas", response_model=list[R.DisciplinaDoAluno],
            summary="Disciplinas do aluno: o plano curricular da classe da turma, com o professor")
def disciplinas_aluno(aid: int, ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    obter(db, Aluno, aid, ctx.estabelecimento_id, "Aluno")
    return disciplinas_do_aluno(db, aid, ctx.estabelecimento_id)


@router.patch("/alunos/{aid}", response_model=R.Aluno)
def editar_aluno(aid: int, dados: AlunoUpd, ctx: Contexto = Depends(editar), db: Session = Depends(get_db)):
    a = obter(db, Aluno, aid, ctx.estabelecimento_id, "Aluno")
    antes = as_dict(a)
    aplicar(a, dados.model_dump(exclude_unset=True))
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Número de processo já usado")
    auditar(db, ctx, "aluno", aid, "update", dados.model_dump(exclude_unset=True, mode="json"), antes)
    db.commit()
    return as_dict(a)


@router.post("/alunos/{aid}/encarregados", response_model=R.AlunoEncarregado, status_code=201)
def ligar_encarregado(aid: int, dados: LigacaoIn, ctx: Contexto = Depends(editar), db: Session = Depends(get_db)):
    obter(db, Aluno, aid, ctx.estabelecimento_id, "Aluno")
    obter(db, Encarregado, dados.encarregado_id, ctx.estabelecimento_id, "Encarregado")
    if dados.responsavel_financeiro:
        db.execute(update(AlunoEncarregado).where(AlunoEncarregado.aluno_id == aid)
                   .values(responsavel_financeiro=False))
    if dados.principal:
        db.execute(update(AlunoEncarregado).where(AlunoEncarregado.aluno_id == aid).values(principal=False))
    lig = db.get(AlunoEncarregado, (aid, dados.encarregado_id)) or AlunoEncarregado(aluno_id=aid)
    aplicar(lig, dados.model_dump())
    db.add(lig)
    db.commit()
    return as_dict(lig)


@router.delete("/alunos/{aid}/encarregados/{eid}", status_code=204)
def desligar_encarregado(aid: int, eid: int, ctx: Contexto = Depends(editar), db: Session = Depends(get_db)):
    obter(db, Aluno, aid, ctx.estabelecimento_id, "Aluno")
    db.execute(delete(AlunoEncarregado).where(AlunoEncarregado.aluno_id == aid, AlunoEncarregado.encarregado_id == eid))
    db.commit()


@router.post("/alunos/{aid}/documentos", response_model=R.DocumentoAluno, status_code=201)
def anexar_documento(aid: int, dados: DocumentoIn, ctx: Contexto = Depends(editar), db: Session = Depends(get_db)):
    obter(db, Aluno, aid, ctx.estabelecimento_id, "Aluno")
    d = DocumentoAluno(aluno_id=aid, **dados.model_dump())
    db.add(d)
    db.commit()
    return as_dict(d)


@router.get("/alunos/{aid}/360", response_model=R.Aluno360, summary="Visão 360° do aluno")
def aluno_360(aid: int, ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    est = ctx.estabelecimento_id
    a = obter(db, Aluno, aid, est, "Aluno")
    m = _matricula_activa(db, aid, est)
    turma = db.get(Turma, m.turma_id) if m else None
    ees = db.execute(select(Encarregado, AlunoEncarregado).join(AlunoEncarregado, AlunoEncarregado.encarregado_id == Encarregado.id)
                     .where(AlunoEncarregado.aluno_id == aid).order_by(AlunoEncarregado.principal.desc()))
    cobr = cobrancas_detalhe(db, est, aluno_id=aid)
    adesoes = db.execute(select(AdesaoServico, Servico.nome).join(Servico, Servico.id == AdesaoServico.servico_id)
                         .where(AdesaoServico.matricula_id == (m.id if m else -1)))
    ocorr = db.scalar(select(func.count()).select_from(Ocorrencia)
                      .where(Ocorrencia.aluno_id == aid, Ocorrencia.estado != "fechada"))
    return {
        "aluno": as_dict(a),
        "turma": {"id": turma.id, "designacao": turma.designacao} if turma else None,
        "matricula_id": m.id if m else None,
        "encarregados": [{"id": e.id, "nome": e.nome_completo, "telefone": e.telefone, "whatsapp": e.whatsapp,
                          "email": e.email, "parentesco": l.parentesco, "principal": l.principal,
                          "responsavel_financeiro": l.responsavel_financeiro} for e, l in ees],
        "financeiro": {
            "cobrancas": cobr,
            "em_divida": sum(x["valor_em_aberto"] for x in cobr if x["estado"] in ("pendente", "parcial")),
            "em_atraso": sum(x["valor_em_aberto"] for x in cobr if x["dias_atraso"] > 0),
            "servicos": [{"id": ad.id, "servico": n, "modalidade": ad.modalidade, "estado": ad.estado}
                         for ad, n in adesoes],
        },
        "pedagogico": {
            "media": media_geral(db, m) if m else None,
            "faltas": total_faltas(db, aid, m.turma_id if m else None),
            "comportamento": comportamento_actual(db, aid),
            "ocorrencias_abertas": ocorr,
        },
    }


# ---------------------------------------------------------------- encarregados
@router.get("/encarregados", response_model=list[R.EncarregadoLista])
def listar_encarregados(q: str | None = None, ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    stmt = select(Encarregado).where(Encarregado.estabelecimento_id == ctx.estabelecimento_id)
    if q:
        stmt = stmt.where(Encarregado.nome_completo.ilike(f"%{q.strip()}%") | (Encarregado.nif == q.strip()))
    ees = db.scalars(stmt.order_by(Encarregado.nome_completo)).all()
    ligs = db.execute(select(AlunoEncarregado.encarregado_id, AlunoEncarregado.parentesco, Aluno.id, Aluno.nome_completo)
                      .join(Aluno, Aluno.id == AlunoEncarregado.aluno_id)
                      .where(AlunoEncarregado.encarregado_id.in_([e.id for e in ees]))).all()
    contas: dict[int, dict] = {}
    for x in cobrancas_detalhe(db, ctx.estabelecimento_id):
        c = contas.setdefault(x["encarregado_id"], {"total_pago": 0.0, "total_em_divida": 0.0, "cobrancas_em_atraso": 0})
        c["total_pago"] += x["valor_pago"]
        c["total_em_divida"] += x["valor_em_aberto"]
        c["cobrancas_em_atraso"] += 1 if x["dias_atraso"] > 0 and x["valor_em_aberto"] > 0 else 0
    out = []
    for e in ees:
        meus = [l for l in ligs if l[0] == e.id]
        conta = contas.get(e.id, {"total_pago": 0.0, "total_em_divida": 0.0, "cobrancas_em_atraso": 0})
        out.append({**as_dict(e), "parentesco": meus[0][1] if meus else None,
                    "educandos": [{"id": l[2], "nome": l[3]} for l in meus], "num_educandos": len(meus),
                    **{k: round(v, 2) if isinstance(v, float) else v for k, v in conta.items()}})
    return out


@router.post("/encarregados", response_model=R.Encarregado, status_code=201)
def criar_encarregado(dados: EncarregadoIn, ctx: Contexto = Depends(criar), db: Session = Depends(get_db)):
    e = Encarregado(estabelecimento_id=ctx.estabelecimento_id, **dados.model_dump(exclude_none=True))
    if not e.whatsapp:
        e.whatsapp = e.telefone
    db.add(e)
    db.flush()
    for canal in ("whatsapp", "sms", "email", "app"):
        db.add(PreferenciaContacto(encarregado_id=e.id, canal=canal, activo=True))
    auditar(db, ctx, "encarregado", e.id, "insert", as_dict(e))
    db.commit()
    return as_dict(e)


@router.get("/encarregados/{eid}", response_model=R.EncarregadoDetalhe)
def ver_encarregado(eid: int, ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    e = obter(db, Encarregado, eid, ctx.estabelecimento_id, "Encarregado")
    educandos = db.execute(select(Aluno, AlunoEncarregado).join(AlunoEncarregado, AlunoEncarregado.aluno_id == Aluno.id)
                           .where(AlunoEncarregado.encarregado_id == eid))
    prefs = db.scalars(select(PreferenciaContacto).where(PreferenciaContacto.encarregado_id == eid))
    return {**as_dict(e),
            "educandos": [{"id": a.id, "nome": a.nome_completo, "parentesco": l.parentesco,
                           "responsavel_financeiro": l.responsavel_financeiro} for a, l in educandos],
            "preferencias": {p.canal: p.activo for p in prefs},
            "em_divida": sum(x["valor_em_aberto"] for x in cobrancas_detalhe(db, ctx.estabelecimento_id,
                                                                             encarregado_id=eid, estado="divida"))}


@router.patch("/encarregados/{eid}", response_model=R.Encarregado)
def editar_encarregado(eid: int, dados: EncarregadoUpd, ctx: Contexto = Depends(editar), db: Session = Depends(get_db)):
    e = obter(db, Encarregado, eid, ctx.estabelecimento_id, "Encarregado")
    antes = as_dict(e)
    aplicar(e, dados.model_dump(exclude_unset=True))
    auditar(db, ctx, "encarregado", eid, "update", dados.model_dump(exclude_unset=True, mode="json"), antes)
    db.commit()
    return as_dict(e)


@router.put("/encarregados/{eid}/preferencias", response_model=R.PreferenciaContacto)
def preferencia(eid: int, dados: PreferenciaIn, ctx: Contexto = Depends(editar), db: Session = Depends(get_db)):
    obter(db, Encarregado, eid, ctx.estabelecimento_id, "Encarregado")
    p = db.get(PreferenciaContacto, (eid, dados.canal)) or PreferenciaContacto(encarregado_id=eid, canal=dados.canal)
    p.activo = dados.activo
    db.add(p)
    db.commit()
    return as_dict(p)


# ---------------------------------------------------------------- matrículas
@router.get("/matriculas", response_model=list[R.MatriculaLista])
def listar_matriculas(ano_id: int | None = None, turma_id: int | None = None, ctx: Contexto = Depends(ver),
                      db: Session = Depends(get_db)):
    est = ctx.estabelecimento_id
    ano_id = ano_id or ano_activo(db, est).id
    stmt = (select(Matricula, Aluno.nome_completo, Turma.designacao, AnoLectivo.designacao)
            .join(Aluno, Aluno.id == Matricula.aluno_id).join(Turma, Turma.id == Matricula.turma_id)
            .join(AnoLectivo, AnoLectivo.id == Matricula.ano_id)
            .where(Matricula.estabelecimento_id == est, Matricula.ano_id == ano_id))
    if turma_id:
        stmt = stmt.where(Matricula.turma_id == turma_id)
    return [{**as_dict(m), "aluno": a, "turma": t, "ano_lectivo": an}
            for m, a, t, an in db.execute(stmt.order_by(Matricula.data_matricula.desc(), Matricula.id.desc()))]


@router.post("/matriculas", response_model=R.MatriculaRegistada, status_code=201)
def matricular(dados: MatriculaIn, ctx: Contexto = Depends(criar), db: Session = Depends(get_db)):
    """Regista a matrícula no ano activo. Se o aluno já estiver matriculado, muda-o de turma."""
    est = ctx.estabelecimento_id
    ano = ano_activo(db, est)
    obter(db, Aluno, dados.aluno_id, est, "Aluno")
    existente = db.scalar(select(Matricula).where(Matricula.aluno_id == dados.aluno_id, Matricula.ano_id == ano.id))
    if existente and existente.estado == "activa" and (
            existente.turma_id == dados.turma_id
            or (dados.turma_id is None and db.get(Turma, existente.turma_id).classe_id == dados.classe_id)):
        raise HTTPException(409, "O aluno já está matriculado nesta turma")
    turma = _turma_do_ano(db, dados.turma_id, est, ano.id, dados.classe_id)
    if existente:
        db.add(MatriculaHistorico(matricula_id=existente.id, turma_anterior_id=existente.turma_id,
                                  turma_nova_id=turma.id, motivo=dados.observacoes or dados.tipo))
        existente.turma_id, existente.estado = turma.id, "activa"
        m = existente
    else:
        m = Matricula(estabelecimento_id=est, aluno_id=dados.aluno_id, ano_id=ano.id, turma_id=turma.id,
                      tipo=dados.tipo, observacoes=dados.observacoes, registado_por=ctx.id)
        db.add(m)
    db.flush()
    auditar(db, ctx, "matricula", m.id, "update" if existente else "insert", as_dict(m))
    db.commit()
    return {**as_dict(m), "turma": turma.designacao, "ano_lectivo": ano.designacao,
            "mudanca_de_turma": existente is not None}


@router.post("/matriculas/{mid}/turma", response_model=R.Matricula)
def mudar_turma(mid: int, dados: MudancaTurmaIn, ctx: Contexto = Depends(editar), db: Session = Depends(get_db)):
    m = obter(db, Matricula, mid, ctx.estabelecimento_id, "Matrícula")
    turma = _turma_do_ano(db, dados.turma_id, ctx.estabelecimento_id, m.ano_id)
    db.add(MatriculaHistorico(matricula_id=m.id, turma_anterior_id=m.turma_id, turma_nova_id=turma.id,
                              motivo=dados.motivo))
    m.turma_id = turma.id
    db.commit()
    return as_dict(m)


@router.post("/matriculas/{mid}/anular", response_model=R.Matricula)
def anular_matricula(mid: int, ctx: Contexto = Depends(editar), db: Session = Depends(get_db)):
    m = obter(db, Matricula, mid, ctx.estabelecimento_id, "Matrícula")
    m.estado = "anulada"
    auditar(db, ctx, "matricula", mid, "update", {"estado": "anulada"})
    db.commit()
    return as_dict(m)


@router.get("/matriculas/{mid}/historico", response_model=list[R.MatriculaHistorico])
def historico(mid: int, ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    obter(db, Matricula, mid, ctx.estabelecimento_id, "Matrícula")
    return [as_dict(h) for h in db.scalars(select(MatriculaHistorico).where(MatriculaHistorico.matricula_id == mid)
                                           .order_by(MatriculaHistorico.data_alteracao))]


@router.get("/matriculas/{mid}/descontos", response_model=list[R.DescontoDaMatricula])
def descontos_matricula(mid: int, ctx: Contexto = Depends(exige("propinas")), db: Session = Depends(get_db)):
    obter(db, Matricula, mid, ctx.estabelecimento_id, "Matrícula")
    rows = db.execute(select(MatriculaDesconto, Desconto).join(Desconto, Desconto.id == MatriculaDesconto.desconto_id)
                      .where(MatriculaDesconto.matricula_id == mid))
    return [{**as_dict(md), "desconto": as_dict(d)} for md, d in rows]


@router.post("/matriculas/{mid}/descontos", response_model=R.MatriculaDesconto, status_code=201)
def aplicar_desconto(mid: int, dados: MatriculaDescontoIn, ctx: Contexto = Depends(exige("propinas", "editar")),
                     db: Session = Depends(get_db)):
    obter(db, Matricula, mid, ctx.estabelecimento_id, "Matrícula")
    obter(db, Desconto, dados.desconto_id, ctx.estabelecimento_id, "Desconto")
    md = MatriculaDesconto(matricula_id=mid, aprovado_por=ctx.id, **dados.model_dump())
    db.add(md)
    auditar(db, ctx, "matricula_desconto", mid, "insert", dados.model_dump(mode="json"))
    db.commit()
    return as_dict(md)



# ---------------------------------------------------------------- CRUD completo (apagar)
crud.registar(router, "/alunos", Aluno, nome="Aluno", modulo="cadastro", resposta=R.Aluno, ops=("apagar",))
crud.registar(router, "/encarregados", Encarregado, nome="Encarregado", modulo="cadastro", resposta=R.Encarregado,
              ops=("apagar",))
