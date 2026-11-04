"""Secretaria: validação dos documentos dos alunos, documentos em falta (com aviso às famílias), documentos exigidos
por classe, modelos de declaração, declarações emitidas a partir do cadastro, ficha de contactos e o painel."""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import crud
from app import respostas as R
from app.common import ano_activo, as_dict, obter
from app.database import get_db
from app.models import (
    Aluno, Candidatura, DocumentoAluno, DocumentoExigido, Matricula, ModeloDeclaracao, Pedido, Turma,
)
from app.schemas import (
    AvisoDocumentosIn, DocumentoExigidoIn, DocumentoExigidoUpd, ModeloDeclaracaoIn, ModeloDeclaracaoUpd,
    ValidarDocumentoIn,
)
from app.security import Contexto, auditar, exige
from app.services import pedidos as svc_pedidos
from app.services import secretaria as svc

router = APIRouter(prefix="/api", tags=["Secretaria"])
ver, editar = exige("cadastro"), exige("cadastro", "editar")


def _alunos(db: Session, ctx: Contexto, turma_id: int | None, classe_id: int | None,
            aluno_ids: list[int] | None = None) -> list[Aluno]:
    """Alunos activos matriculados no ano activo (filtrados por turma, classe ou lista)."""
    ano = ano_activo(db, ctx.estabelecimento_id)
    stmt = (select(Aluno).join(Matricula, Matricula.aluno_id == Aluno.id).join(Turma, Turma.id == Matricula.turma_id)
            .where(Aluno.estabelecimento_id == ctx.estabelecimento_id, Aluno.estado == "activo",
                   Matricula.ano_id == ano.id, Matricula.estado == "activa"))
    if turma_id:
        stmt = stmt.where(Matricula.turma_id == turma_id)
    if classe_id:
        stmt = stmt.where(Turma.classe_id == classe_id)
    if aluno_ids:
        stmt = stmt.where(Aluno.id.in_(aluno_ids))
    return list(db.scalars(stmt.order_by(Aluno.nome_completo)))


def _situacao(db: Session, a: Aluno) -> dict:
    s = svc.situacao(db, a)
    return {"aluno_id": a.id, "aluno": a.nome_completo, "numero_processo": a.numero_processo,
            "turma": s["turma"].designacao if s["turma"] else None, "em_falta": s["em_falta"],
            "por_validar": len(s["por_validar"])}


# ---------------------------------------------------------------- documentos dos alunos
@router.get("/documentos", response_model=list[R.DocumentoComAluno],
            summary="Documentos dos alunos (estado=recebido: por validar)")
def documentos(estado: str | None = None, aluno_id: int | None = None, tipo: str | None = None,
               ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    stmt = (select(DocumentoAluno, Aluno).join(Aluno, Aluno.id == DocumentoAluno.aluno_id)
            .where(Aluno.estabelecimento_id == ctx.estabelecimento_id))
    for coluna, valor in ((DocumentoAluno.estado, estado), (DocumentoAluno.aluno_id, aluno_id), (DocumentoAluno.tipo, tipo)):
        if valor:
            stmt = stmt.where(coluna == valor)
    return [{**as_dict(d), "aluno": a.nome_completo, "nome_tipo": svc.nome_documento(db, ctx.estabelecimento_id, d.tipo)}
            for d, a in db.execute(stmt.order_by(DocumentoAluno.criado_em.desc()).limit(500))]


@router.post("/alunos/{aid}/documentos/{did}/validar", response_model=R.DocumentoAluno,
             summary="Aceita ou rejeita um documento (rejeitar avisa a família com o motivo)")
def validar(aid: int, did: int, dados: ValidarDocumentoIn, ctx: Contexto = Depends(editar), db: Session = Depends(get_db)):
    obter(db, Aluno, aid, ctx.estabelecimento_id, "Aluno")
    d = db.get(DocumentoAluno, did)
    if d is None or d.aluno_id != aid:
        raise HTTPException(404, f"Documento {did} não encontrado")
    svc.validar_documento(db, d, dados.estado, dados.observacao, ctx.id)
    auditar(db, ctx, "documento_aluno", did, "update", {"estado": dados.estado, "observacao": dados.observacao})
    db.commit()
    return as_dict(d)


@router.get("/documentos/em-falta", response_model=list[R.SituacaoDocumentos],
            summary="Alunos do ano activo com documentos exigidos em falta ou por validar")
def em_falta(turma_id: int | None = None, classe_id: int | None = None, ctx: Contexto = Depends(ver),
             db: Session = Depends(get_db)):
    linhas = [_situacao(db, a) for a in _alunos(db, ctx, turma_id, classe_id)]
    return [x for x in linhas if x["em_falta"] or x["por_validar"]]


@router.post("/documentos/em-falta/avisar", response_model=R.AvisoDocumentos,
             summary="Avisa as famílias (app + WhatsApp) dos documentos que faltam a cada aluno")
def avisar(dados: AvisoDocumentosIn, ctx: Contexto = Depends(editar), db: Session = Depends(get_db)):
    alunos = avisados = 0
    for a in _alunos(db, ctx, dados.turma_id, None, dados.aluno_ids):
        falta = [f["nome"] for f in svc.situacao(db, a)["em_falta"] if f["obrigatorio"]]
        if falta:
            alunos += 1
            avisados += svc.avisar_encarregados(db, a, "Documentos em falta",
                                                f"Faltam documentos de {a.nome_completo}: {', '.join(falta)}. "
                                                "Pode carregá-los no portal, em Documentos do educando.")
    auditar(db, ctx, "documento_aluno", None, "notify", {"alunos": alunos})
    db.commit()
    return {"alunos": alunos, "avisados": avisados}


# ---------------------------------------------------------------- documentos exigidos
@router.get("/documentos-exigidos", response_model=list[R.DocumentoExigido],
            summary="Documentos pedidos aos alunos (classe vazia = todas as classes)")
def exigidos(ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    return [as_dict(e) for e in db.scalars(select(DocumentoExigido).where(
        DocumentoExigido.estabelecimento_id == ctx.estabelecimento_id).order_by(DocumentoExigido.id))]


def _gravar(db: Session, obj) -> dict:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Já existe um registo com este código") from None
    return as_dict(obj)


@router.post("/documentos-exigidos", response_model=R.DocumentoExigido, status_code=201)
def criar_exigido(dados: DocumentoExigidoIn, ctx: Contexto = Depends(editar), db: Session = Depends(get_db)):
    crud._validar_referencias(db, DocumentoExigido, dados.model_dump(), ctx.estabelecimento_id)
    e = DocumentoExigido(estabelecimento_id=ctx.estabelecimento_id, **dados.model_dump())
    db.add(e)
    return _gravar(db, e)


@router.patch("/documentos-exigidos/{id_}", response_model=R.DocumentoExigido)
def editar_exigido(id_: int, dados: DocumentoExigidoUpd, ctx: Contexto = Depends(editar), db: Session = Depends(get_db)):
    e = obter(db, DocumentoExigido, id_, ctx.estabelecimento_id, "Documento exigido")
    mudancas = dados.model_dump(exclude_unset=True)
    crud._validar_referencias(db, DocumentoExigido, mudancas, ctx.estabelecimento_id)
    for k, v in mudancas.items():
        setattr(e, k, v)
    return _gravar(db, e)


# ---------------------------------------------------------------- modelos de declaração
@router.get("/modelos-declaracao", response_model=list[R.ModeloDeclaracao])
def modelos(ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    return [as_dict(m) for m in db.scalars(select(ModeloDeclaracao).where(
        ModeloDeclaracao.estabelecimento_id == ctx.estabelecimento_id).order_by(ModeloDeclaracao.id))]


@router.get("/modelos-declaracao/variaveis", response_model=list[R.VariavelModelo],
            summary="Variáveis que se podem usar no texto, ex.: {nome}, {classe}")
def variaveis(ctx: Contexto = Depends(ver)):
    return [{"variavel": "{" + k + "}", "descricao": v} for k, v in svc.VARIAVEIS.items()]


@router.post("/modelos-declaracao", response_model=R.ModeloDeclaracao, status_code=201)
def criar_modelo(dados: ModeloDeclaracaoIn, ctx: Contexto = Depends(editar), db: Session = Depends(get_db)):
    svc.validar_modelo(dados.texto)
    m = ModeloDeclaracao(estabelecimento_id=ctx.estabelecimento_id, **dados.model_dump())
    db.add(m)
    return _gravar(db, m)


@router.patch("/modelos-declaracao/{id_}", response_model=R.ModeloDeclaracao)
def editar_modelo(id_: int, dados: ModeloDeclaracaoUpd, ctx: Contexto = Depends(editar), db: Session = Depends(get_db)):
    m = obter(db, ModeloDeclaracao, id_, ctx.estabelecimento_id, "Modelo de declaração")
    mudancas = dados.model_dump(exclude_unset=True)
    if mudancas.get("texto"):
        svc.validar_modelo(mudancas["texto"])
    for k, v in mudancas.items():
        setattr(m, k, v)
    auditar(db, ctx, "modelo_declaracao", id_, "update", mudancas)
    return _gravar(db, m)


@router.get("/alunos/{aid}/declaracao.pdf", responses=R.PDF,
            summary="Emite uma declaração (modelo escolhido) a partir do cadastro, sem redigitar")
def declaracao(aid: int, modelo: str = "matricula", finalidade: str | None = None, ctx: Contexto = Depends(ver),
               db: Session = Depends(get_db)):
    a = obter(db, Aluno, aid, ctx.estabelecimento_id, "Aluno")
    pdf = svc.declaracao(db, a, svc.modelo(db, ctx.estabelecimento_id, modelo), finalidade)
    auditar(db, ctx, "aluno", aid, "export", {"declaracao": modelo})
    db.commit()
    return Response(pdf, media_type="application/pdf")


# ---------------------------------------------------------------- contactos
@router.get("/alunos/{aid}/ficha", response_model=R.FichaAluno,
            summary="Dados do aluno e todos os contactos (pai, mãe, encarregado…), com avisos de contactos em falta")
def ficha(aid: int, ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    return svc.ficha(db, obter(db, Aluno, aid, ctx.estabelecimento_id, "Aluno"))


@router.get("/contactos/avisos", response_model=list[R.AvisoContactos],
            summary="Alunos do ano activo com contactos em falta, duplicados ou inválidos")
def avisos_contactos(turma_id: int | None = None, ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    linhas = []
    for a in _alunos(db, ctx, turma_id, None):
        avisos = svc.ficha(db, a)["avisos"]
        if avisos:
            linhas.append({"aluno_id": a.id, "aluno": a.nome_completo, "avisos": avisos})
    return linhas


# ---------------------------------------------------------------- painel
@router.get("/secretaria/painel", response_model=R.PainelSecretaria,
            summary="Documentos por validar e em falta, pedidos por serviço, declarações, candidaturas e renovações")
def painel(ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    est = ctx.estabelecimento_id
    abertos = db.execute(select(Pedido.tipo, Pedido.servico, Pedido.estado).where(
        Pedido.estabelecimento_id == est, Pedido.estado.in_((*svc_pedidos.ABERTOS, "aprovado")))).all()
    alunos = _alunos(db, ctx, None, None) if ano_activo(db, est, obrigatorio=False) else []
    situacoes = [svc.situacao(db, a) for a in alunos]
    por_servico: dict[str, int] = {}
    for _, servico, estado in abertos:
        if estado in svc_pedidos.ABERTOS:
            por_servico[servico] = por_servico.get(servico, 0) + 1
    candidaturas = db.scalar(select(func.count()).select_from(Candidatura).where(
        Candidatura.estabelecimento_id == est, Candidatura.estado.not_in(("concluida", "rejeitada", "cancelada"))))
    return {
        "documentos_por_validar": sum(len(s["por_validar"]) for s in situacoes),
        "alunos_com_documentos_em_falta": sum(1 for s in situacoes if any(f["obrigatorio"] for f in s["em_falta"])),
        "pedidos_abertos": sum(por_servico.values()), "pedidos_por_servico": por_servico,
        "aguardam_familia": sum(1 for _, _, e in abertos if e == "aguarda_informacao"),
        "declaracoes_por_emitir": sum(1 for t, _, e in abertos if t == "declaracao" and e in svc_pedidos.ABERTOS),
        "declaracoes_por_entregar": sum(1 for t, _, e in abertos if t == "declaracao" and e == "aprovado"),
        "renovacoes_pendentes": sum(1 for t, _, e in abertos if t == "matricula" and e in svc_pedidos.ABERTOS),
        "transferencias_pendentes": sum(1 for t, _, e in abertos if t == "transferencia" and e in svc_pedidos.ABERTOS),
        "candidaturas_em_curso": candidaturas or 0,
        "alunos_com_avisos_de_contacto": sum(1 for a in alunos if svc.ficha(db, a)["avisos"]),
    }


crud.registar(router, "/documentos-exigidos", DocumentoExigido, nome="Documento exigido", modulo="cadastro",
              resposta=R.DocumentoExigido, ops=("ver", "apagar"))
crud.registar(router, "/modelos-declaracao", ModeloDeclaracao, nome="Modelo de declaração", modulo="cadastro",
              resposta=R.ModeloDeclaracao, ops=("ver", "apagar"))
