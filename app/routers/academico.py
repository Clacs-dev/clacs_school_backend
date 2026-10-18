from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import respostas as R
from app.common import ano_activo, aplicar, as_dict, obter
from app.database import get_db
from app.models import (
    Aluno, AnoLectivo, AtribuicaoDocente, Classe, ClasseDisciplina, Disciplina, Estabelecimento, Funcionario,
    Matricula, NivelEnsino, Parametro, PeriodoLectivo, ResultadoFinal, Sala, Turma,
)
from app.schemas import (
    AnoIn, AtribuicaoIn, ClasseDisciplinaIn, ClasseIn, DisciplinaIn, Entrada, com_exemplo, EstabelecimentoUpd, FuncionarioIn,
    FuncionarioUpd, NivelIn, ParametroIn, PeriodoIn, SalaIn, TurmaIn, TurmaUpd,
)
from app.security import Contexto, auditar, exige, turmas_permitidas
from app.services import fecho_ano

router = APIRouter(prefix="/api", tags=["Académico"])
ver, criar, editar = exige("academico"), exige("academico", "criar"), exige("academico", "editar")


def gravar(db: Session, obj, msg: str = "Registo duplicado"):
    try:
        db.add(obj)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, msg)
    return as_dict(obj)


def trimestres(ano: AnoLectivo) -> list[PeriodoLectivo]:
    """1.º Set–Dez, 2.º Jan–Mar, 3.º Abr–fim, como indicado no protótipo."""
    a, b = ano.data_inicio.year, ano.data_fim.year
    return [
        PeriodoLectivo(ano_id=ano.id, designacao="1.º Trimestre", ordem=1, data_inicio=ano.data_inicio, data_fim=date(a, 12, 20)),
        PeriodoLectivo(ano_id=ano.id, designacao="2.º Trimestre", ordem=2, data_inicio=date(b, 1, 6), data_fim=date(b, 3, 31)),
        PeriodoLectivo(ano_id=ano.id, designacao="3.º Trimestre", ordem=3, data_inicio=date(b, 4, 7), data_fim=ano.data_fim),
    ]


# ---------------------------------------------------------------- estabelecimento e parâmetros
@router.get("/estabelecimento", response_model=R.Estabelecimento)
def estabelecimento(ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    return as_dict(db.get(Estabelecimento, ctx.estabelecimento_id))


@router.patch("/estabelecimento", response_model=R.Estabelecimento)
def editar_estabelecimento(dados: EstabelecimentoUpd, ctx: Contexto = Depends(editar), db: Session = Depends(get_db)):
    e = db.get(Estabelecimento, ctx.estabelecimento_id)
    aplicar(e, dados.model_dump(exclude_unset=True))
    db.commit()
    return as_dict(e)


@router.get("/parametros", response_model=list[R.Parametro])
def parametros(ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    return [as_dict(p) for p in db.scalars(select(Parametro).where(Parametro.estabelecimento_id == ctx.estabelecimento_id))]


@router.put("/parametros/{chave}", response_model=R.Parametro)
def definir_parametro(chave: str, dados: ParametroIn, ctx: Contexto = Depends(editar), db: Session = Depends(get_db)):
    p = db.get(Parametro, (ctx.estabelecimento_id, chave)) or Parametro(estabelecimento_id=ctx.estabelecimento_id, chave=chave)
    p.valor, p.descricao = dados.valor, dados.descricao or p.descricao
    db.add(p)
    db.commit()
    return as_dict(p)


# ---------------------------------------------------------------- anos e períodos
@router.get("/anos-lectivos", response_model=list[R.AnoLectivo])
def anos(ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    return [as_dict(a) for a in db.scalars(select(AnoLectivo).where(AnoLectivo.estabelecimento_id == ctx.estabelecimento_id)
                                           .order_by(AnoLectivo.data_inicio.desc()))]


@router.post("/anos-lectivos", response_model=R.AnoLectivo, status_code=201)
def criar_ano(dados: AnoIn, ctx: Contexto = Depends(criar), db: Session = Depends(get_db)):
    if dados.data_fim <= dados.data_inicio:
        raise HTTPException(422, "A data de fim tem de ser posterior à de início")
    ano = AnoLectivo(estabelecimento_id=ctx.estabelecimento_id, designacao=dados.designacao,
                     data_inicio=dados.data_inicio, data_fim=dados.data_fim)
    res = gravar(db, ano, "Já existe um ano lectivo com esta designação")
    if dados.criar_trimestres:
        db.add_all(trimestres(ano))
        db.commit()
    return res


@router.post("/anos-lectivos/{ano_id}/activar", response_model=R.AnoLectivo)
def activar_ano(ano_id: int, ctx: Contexto = Depends(editar), db: Session = Depends(get_db)):
    ano = obter(db, AnoLectivo, ano_id, ctx.estabelecimento_id, "Ano lectivo")
    db.execute(update(AnoLectivo).where(AnoLectivo.estabelecimento_id == ctx.estabelecimento_id,
                                        AnoLectivo.estado == "activo", AnoLectivo.id != ano_id)
               .values(estado="encerrado"))
    db.flush()
    ano.estado = "activo"
    auditar(db, ctx, "ano_lectivo", ano_id, "update", {"estado": "activo"})
    db.commit()
    return as_dict(ano)


@router.get("/anos-lectivos/{ano_id}/periodos", response_model=list[R.PeriodoLectivo])
def periodos(ano_id: int, ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    obter(db, AnoLectivo, ano_id, ctx.estabelecimento_id, "Ano lectivo")
    return [as_dict(p) for p in db.scalars(select(PeriodoLectivo).where(PeriodoLectivo.ano_id == ano_id)
                                           .order_by(PeriodoLectivo.ordem))]


@router.post("/anos-lectivos/{ano_id}/periodos", response_model=R.PeriodoLectivo, status_code=201)
def criar_periodo(ano_id: int, dados: PeriodoIn, ctx: Contexto = Depends(criar), db: Session = Depends(get_db)):
    obter(db, AnoLectivo, ano_id, ctx.estabelecimento_id, "Ano lectivo")
    return gravar(db, PeriodoLectivo(ano_id=ano_id, **dados.model_dump()), "Já existe um período com essa ordem")


# ---------------------------------------------------------------- níveis, classes, salas, disciplinas
@router.get("/niveis", response_model=list[R.NivelEnsino])
def niveis(ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    return [as_dict(n) for n in db.scalars(select(NivelEnsino).where(NivelEnsino.estabelecimento_id == ctx.estabelecimento_id)
                                           .order_by(NivelEnsino.ordem))]


@router.post("/niveis", response_model=R.NivelEnsino, status_code=201)
def criar_nivel(dados: NivelIn, ctx: Contexto = Depends(criar), db: Session = Depends(get_db)):
    return gravar(db, NivelEnsino(estabelecimento_id=ctx.estabelecimento_id, **dados.model_dump()))


@router.get("/classes", response_model=list[R.ClasseComNivel])
def classes(ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    rows = db.execute(select(Classe, NivelEnsino.nome).join(NivelEnsino, NivelEnsino.id == Classe.nivel_id)
                      .where(NivelEnsino.estabelecimento_id == ctx.estabelecimento_id).order_by(Classe.ordem))
    return [{**as_dict(c), "nivel": n} for c, n in rows]


@router.post("/classes", response_model=R.Classe, status_code=201)
def criar_classe(dados: ClasseIn, ctx: Contexto = Depends(criar), db: Session = Depends(get_db)):
    obter(db, NivelEnsino, dados.nivel_id, ctx.estabelecimento_id, "Nível de ensino")
    return gravar(db, Classe(**dados.model_dump()))


def _classe(db: Session, classe_id: int, est: int) -> Classe:
    c = obter(db, Classe, classe_id, nome="Classe")
    obter(db, NivelEnsino, c.nivel_id, est, "Classe")
    return c


@router.get("/classes/{classe_id}/disciplinas", response_model=list[R.DisciplinaDoPlano])
def plano_curricular(classe_id: int, ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    _classe(db, classe_id, ctx.estabelecimento_id)
    rows = db.execute(select(Disciplina, ClasseDisciplina.carga_horaria)
                      .join(ClasseDisciplina, ClasseDisciplina.disciplina_id == Disciplina.id)
                      .where(ClasseDisciplina.classe_id == classe_id).order_by(Disciplina.nome))
    return [{**as_dict(d), "carga_horaria": ch} for d, ch in rows]


@router.post("/classes/{classe_id}/disciplinas", response_model=R.ClasseDisciplina, status_code=201)
def associar_disciplina(classe_id: int, dados: ClasseDisciplinaIn, ctx: Contexto = Depends(criar),
                        db: Session = Depends(get_db)):
    _classe(db, classe_id, ctx.estabelecimento_id)
    obter(db, Disciplina, dados.disciplina_id, ctx.estabelecimento_id, "Disciplina")
    return gravar(db, ClasseDisciplina(classe_id=classe_id, **dados.model_dump()), "Disciplina já associada")


@router.get("/salas", response_model=list[R.Sala])
def salas(ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    return [as_dict(s) for s in db.scalars(select(Sala).where(Sala.estabelecimento_id == ctx.estabelecimento_id)
                                           .order_by(Sala.designacao))]


@router.post("/salas", response_model=R.Sala, status_code=201)
def criar_sala(dados: SalaIn, ctx: Contexto = Depends(criar), db: Session = Depends(get_db)):
    return gravar(db, Sala(estabelecimento_id=ctx.estabelecimento_id, **dados.model_dump()), "Esta sala já existe")


@router.get("/disciplinas", response_model=list[R.Disciplina])
def disciplinas(ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    return [as_dict(d) for d in db.scalars(select(Disciplina).where(Disciplina.estabelecimento_id == ctx.estabelecimento_id)
                                           .order_by(Disciplina.nome))]


@router.post("/disciplinas", response_model=R.Disciplina, status_code=201)
def criar_disciplina(dados: DisciplinaIn, ctx: Contexto = Depends(criar), db: Session = Depends(get_db)):
    return gravar(db, Disciplina(estabelecimento_id=ctx.estabelecimento_id, **dados.model_dump()), "Esta disciplina já existe")


# ---------------------------------------------------------------- funcionários
@router.get("/funcionarios", response_model=list[R.Funcionario])
def funcionarios(cargo: str | None = None, ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    stmt = select(Funcionario).where(Funcionario.estabelecimento_id == ctx.estabelecimento_id)
    if cargo:
        stmt = stmt.where(Funcionario.cargo == cargo)
    return [as_dict(f) for f in db.scalars(stmt.order_by(Funcionario.nome_completo))]


@router.post("/funcionarios", response_model=R.Funcionario, status_code=201)
def criar_funcionario(dados: FuncionarioIn, ctx: Contexto = Depends(criar), db: Session = Depends(get_db)):
    return gravar(db, Funcionario(estabelecimento_id=ctx.estabelecimento_id, **dados.model_dump()))


@router.patch("/funcionarios/{fid}", response_model=R.Funcionario)
def editar_funcionario(fid: int, dados: FuncionarioUpd, ctx: Contexto = Depends(editar), db: Session = Depends(get_db)):
    f = obter(db, Funcionario, fid, ctx.estabelecimento_id, "Funcionário")
    aplicar(f, dados.model_dump(exclude_unset=True))
    db.commit()
    return as_dict(f)


# ---------------------------------------------------------------- turmas
def turmas_com_ocupacao(db: Session, est: int, ano_id: int | None) -> list[dict]:
    ocup = (select(Matricula.turma_id, func.count().label("n")).where(Matricula.estado == "activa")
            .group_by(Matricula.turma_id).subquery())
    stmt = (select(Turma, Classe.designacao, Sala.designacao, Funcionario.nome_completo, func.coalesce(ocup.c.n, 0))
            .join(Classe, Classe.id == Turma.classe_id)
            .outerjoin(Sala, Sala.id == Turma.sala_id)
            .outerjoin(Funcionario, Funcionario.id == Turma.director_turma_id)
            .outerjoin(ocup, ocup.c.turma_id == Turma.id)
            .where(Turma.estabelecimento_id == est))
    if ano_id:
        stmt = stmt.where(Turma.ano_id == ano_id)
    res = []
    for t, classe, sala, director, n in db.execute(stmt.order_by(Classe.ordem, Turma.designacao)):
        res.append({**as_dict(t), "classe": classe, "sala": sala, "director_turma": director,
                    "alunos": n, "vagas": max(0, t.capacidade - n), "cheia": n >= t.capacidade})
    return res


@router.get("/turmas", response_model=list[R.TurmaComOcupacao])
def turmas(ano_id: int | None = None, ctx: Contexto = Depends(exige("cadastro")), db: Session = Depends(get_db)):
    if ano_id is None:
        ano = ano_activo(db, ctx.estabelecimento_id, obrigatorio=False)
        ano_id = ano.id if ano else None
    lista = turmas_com_ocupacao(db, ctx.estabelecimento_id, ano_id)
    permitidas = turmas_permitidas(db, ctx)
    return lista if permitidas is None else [t for t in lista if t["id"] in permitidas]


@router.post("/turmas", response_model=R.Turma, status_code=201)
def criar_turma(dados: TurmaIn, ctx: Contexto = Depends(criar), db: Session = Depends(get_db)):
    est = ctx.estabelecimento_id
    ano_id = dados.ano_id or ano_activo(db, est).id
    obter(db, AnoLectivo, ano_id, est, "Ano lectivo")
    _classe(db, dados.classe_id, est)
    if dados.sala_id:
        obter(db, Sala, dados.sala_id, est, "Sala")
    if dados.director_turma_id:
        obter(db, Funcionario, dados.director_turma_id, est, "Funcionário")
    t = Turma(estabelecimento_id=est, **{**dados.model_dump(), "ano_id": ano_id})
    res = gravar(db, t, "Esta turma já existe neste ano lectivo")
    auditar(db, ctx, "turma", t.id, "insert", res)
    db.commit()
    return res


@router.patch("/turmas/{tid}", response_model=R.Turma)
def editar_turma(tid: int, dados: TurmaUpd, ctx: Contexto = Depends(editar), db: Session = Depends(get_db)):
    t = obter(db, Turma, tid, ctx.estabelecimento_id, "Turma")
    aplicar(t, dados.model_dump(exclude_unset=True))
    return gravar(db, t, "Esta turma já existe neste ano lectivo")


@router.get("/turmas/{tid}/atribuicoes", response_model=list[R.AtribuicaoComNomes])
def atribuicoes(tid: int, ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    obter(db, Turma, tid, ctx.estabelecimento_id, "Turma")
    rows = db.execute(select(AtribuicaoDocente, Disciplina.nome, Funcionario.nome_completo)
                      .join(Disciplina, Disciplina.id == AtribuicaoDocente.disciplina_id)
                      .join(Funcionario, Funcionario.id == AtribuicaoDocente.funcionario_id)
                      .where(AtribuicaoDocente.turma_id == tid).order_by(Disciplina.nome))
    return [{**as_dict(a), "disciplina": d, "professor": f} for a, d, f in rows]


@router.put("/turmas/{tid}/atribuicoes", response_model=R.AtribuicaoDocente, status_code=200)
def atribuir(tid: int, dados: AtribuicaoIn, ctx: Contexto = Depends(editar), db: Session = Depends(get_db)):
    est = ctx.estabelecimento_id
    obter(db, Turma, tid, est, "Turma")
    obter(db, Disciplina, dados.disciplina_id, est, "Disciplina")
    obter(db, Funcionario, dados.funcionario_id, est, "Funcionário")
    a = db.scalar(select(AtribuicaoDocente).where(AtribuicaoDocente.turma_id == tid,
                                                  AtribuicaoDocente.disciplina_id == dados.disciplina_id))
    if a:
        a.funcionario_id = dados.funcionario_id
    else:
        a = AtribuicaoDocente(turma_id=tid, **dados.model_dump())
    return gravar(db, a)


# ---------------------------------------------------------------- fecho do ano e passagem
@com_exemplo({"de": 1, "para": 7})
class ParTurmas(Entrada):
    de: int
    para: int


@com_exemplo({"de_ano_id": 2, "turmas": [{"de": 1, "para": 6}], "repetentes": [], "copiar_servicos": True})
class TransitarIn(Entrada):
    de_ano_id: int
    turmas: list[ParTurmas]          # aprovados: turma do ano anterior → turma do novo ano
    repetentes: list[ParTurmas] = []  # reprovados: turma do ano anterior → turma do novo ano
    copiar_servicos: bool = True     # alimentação, refeitório…


@router.post("/anos-lectivos/{ano_id}/fechar", response_model=R.ResultadoFecho, summary="Fecha o ano: média final e situação de cada aluno")
def fechar_ano(ano_id: int, simular: bool = True, ctx: Contexto = Depends(editar), db: Session = Depends(get_db)):
    """Com simular=true (por omissão) mostra o resultado sem gravar. Parâmetros: nota_minima (10), faltas_maximas."""
    ano = obter(db, AnoLectivo, ano_id, ctx.estabelecimento_id, "Ano lectivo")
    if ano.estado == "encerrado" and not simular:
        raise HTTPException(409, "Este ano lectivo já está encerrado")
    res = fecho_ano.fechar_ano(db, ctx.estabelecimento_id, ano, simular)
    if not simular:
        auditar(db, ctx, "ano_lectivo", ano_id, "update", {"fecho": res["totais"]})
        db.commit()
    return res


@router.get("/anos-lectivos/{ano_id}/resultados", response_model=list[R.ResultadoComNomes], summary="Resultados finais gravados do ano")
def resultados(ano_id: int, ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    obter(db, AnoLectivo, ano_id, ctx.estabelecimento_id, "Ano lectivo")
    rows = db.execute(select(ResultadoFinal, Aluno.nome_completo, Turma.designacao)
                      .join(Matricula, Matricula.id == ResultadoFinal.matricula_id)
                      .join(Aluno, Aluno.id == Matricula.aluno_id).join(Turma, Turma.id == Matricula.turma_id)
                      .where(Matricula.ano_id == ano_id).order_by(Turma.designacao, Aluno.nome_completo))
    return [{**as_dict(r), "aluno": a, "turma": t} for r, a, t in rows]


@router.post("/anos-lectivos/{ano_id}/transitar", response_model=R.ResultadoTransicao, summary="Matricula no novo ano os alunos do ano anterior")
def transitar(ano_id: int, dados: TransitarIn, ctx: Contexto = Depends(criar), db: Session = Depends(get_db)):
    para = obter(db, AnoLectivo, ano_id, ctx.estabelecimento_id, "Ano lectivo")
    de = obter(db, AnoLectivo, dados.de_ano_id, ctx.estabelecimento_id, "Ano lectivo")
    if de.estado != "encerrado":
        raise HTTPException(409, f"Feche primeiro o ano {de.designacao}")
    res = fecho_ano.transitar(db, ctx.estabelecimento_id, de, para, {p.de: p.para for p in dados.turmas},
                              {p.de: p.para for p in dados.repetentes}, dados.copiar_servicos, ctx.id)
    auditar(db, ctx, "matricula", None, "insert", {"transicao": f"{de.designacao} → {para.designacao}",
                                                   "criadas": res["criadas"]})
    db.commit()
    return res
