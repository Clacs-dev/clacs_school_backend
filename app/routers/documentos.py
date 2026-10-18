"""Ficheiros enviados (documentos, fotos, comprovativos) e documentos PDF."""
import re
import unicodedata
from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import armazenamento
from app import respostas as R
from app.common import as_dict, obter
from app.database import get_db
from app.models import Aluno, AnoLectivo, DocumentoAluno, Matricula, Pagamento, PeriodoLectivo, Turma
from app.security import Contexto, auditar, contexto_actual, exige, verificar_turma
from app.services import documentos as docs

router = APIRouter(prefix="/api", tags=["Documentos e ficheiros"])


def _slug(nome: str) -> str:
    """Nome de ficheiro só com letras simples ("pauta-5.ª A" → "pauta-5a-a"): os cabeçalhos HTTP não aceitam acentos."""
    simples = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", simples.lower()).strip("-") or "documento"


def _pdf(conteudo: bytes, nome: str) -> Response:
    return Response(conteudo, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{_slug(nome)}-{date.today():%Y%m%d}.pdf"'})


# ---------------------------------------------------------------- envio de ficheiros
@router.post("/alunos/{aid}/documentos/ficheiro", response_model=R.DocumentoAluno, status_code=201, summary="Anexa um documento (BI, certificado…)")
async def anexar_ficheiro(aid: int, tipo: str = Form(...), validade: date | None = Form(None),
                          ficheiro: UploadFile = File(...), ctx: Contexto = Depends(exige("cadastro", "editar")),
                          db: Session = Depends(get_db)):
    obter(db, Aluno, aid, ctx.estabelecimento_id, "Aluno")
    chave = armazenamento.guardar(ctx.estabelecimento_id, "documentos", await ficheiro.read(),
                                  ficheiro.content_type, ficheiro.filename)
    d = DocumentoAluno(aluno_id=aid, tipo=tipo, ficheiro_url=armazenamento.url(chave), validade=validade)
    db.add(d)
    auditar(db, ctx, "documento_aluno", aid, "insert", {"tipo": tipo, "ficheiro": chave})
    db.commit()
    return as_dict(d)


@router.post("/alunos/{aid}/foto", response_model=R.Foto, summary="Foto do aluno (JPG, PNG ou WEBP)")
async def foto(aid: int, ficheiro: UploadFile = File(...), ctx: Contexto = Depends(exige("cadastro", "editar")),
               db: Session = Depends(get_db)):
    a = obter(db, Aluno, aid, ctx.estabelecimento_id, "Aluno")
    if not (ficheiro.content_type or "").startswith("image/"):
        raise HTTPException(415, "A foto tem de ser uma imagem")
    chave = armazenamento.guardar(ctx.estabelecimento_id, "fotos", await ficheiro.read(), ficheiro.content_type, ficheiro.filename)
    a.foto_url = armazenamento.url(chave)
    db.commit()
    return {"foto_url": a.foto_url}


@router.post("/pagamentos/{pid}/comprovativo", response_model=R.Comprovativo, summary="Anexa o comprovativo de um pagamento")
async def comprovativo(pid: int, ficheiro: UploadFile = File(...), ctx: Contexto = Depends(exige("propinas", "criar")),
                       db: Session = Depends(get_db)):
    pg = obter(db, Pagamento, pid, ctx.estabelecimento_id, "Pagamento")
    chave = armazenamento.guardar(ctx.estabelecimento_id, "comprovativos", await ficheiro.read(),
                                  ficheiro.content_type, ficheiro.filename)
    pg.comprovativo_url = armazenamento.url(chave)
    db.commit()
    return {"comprovativo_url": pg.comprovativo_url}


PERMISSAO_POR_CATEGORIA = {"documentos": "cadastro", "fotos": "cadastro", "comprovativos": "propinas", "relatorios": "painel"}


@router.get("/ficheiros/{chave:path}", responses=R.QUALQUER_FICHEIRO, summary="Descarrega um ficheiro enviado ou gerado")
def descarregar(chave: str, ctx: Contexto = Depends(contexto_actual)):
    categoria = chave.split("/")[1] if chave.count("/") == 2 else ""
    modulo = PERMISSAO_POR_CATEGORIA.get(categoria)
    if not modulo or not ctx.pode(modulo, "ver"):
        raise HTTPException(404, "Ficheiro não encontrado")
    return FileResponse(armazenamento.caminho(chave, ctx.estabelecimento_id))


# ---------------------------------------------------------------- PDFs
@router.get("/pagamentos/{pid}/recibo.pdf", responses=R.PDF, summary="Recibo do pagamento (documento interno)")
def recibo_pdf(pid: int, ctx: Contexto = Depends(exige("propinas")), db: Session = Depends(get_db)):
    pg = obter(db, Pagamento, pid, ctx.estabelecimento_id, "Pagamento")
    return _pdf(docs.recibo(db, pg), f"recibo-{pid:06d}")


@router.get("/alunos/{aid}/extracto.pdf", responses=R.PDF, summary="Extracto de conta corrente do aluno")
def extracto_pdf(aid: int, ctx: Contexto = Depends(exige("propinas")), db: Session = Depends(get_db)):
    a = obter(db, Aluno, aid, ctx.estabelecimento_id, "Aluno")
    return _pdf(docs.extracto(db, a), f"extracto-{a.numero_processo}")


def _periodo(db: Session, periodo_id: int, est: int) -> PeriodoLectivo:
    p = obter(db, PeriodoLectivo, periodo_id, nome="Período")
    obter(db, AnoLectivo, p.ano_id, est, "Período")
    return p


@router.get("/alunos/{aid}/boletim.pdf", responses=R.PDF, summary="Boletim de avaliação do aluno num período")
def boletim_pdf(aid: int, periodo_id: int, ctx: Contexto = Depends(exige("pedagogico")), db: Session = Depends(get_db)):
    a = obter(db, Aluno, aid, ctx.estabelecimento_id, "Aluno")
    p = _periodo(db, periodo_id, ctx.estabelecimento_id)
    m = db.scalar(select(Matricula).where(Matricula.aluno_id == aid, Matricula.ano_id == p.ano_id))
    if not m:
        raise HTTPException(404, "O aluno não tem matrícula nesse ano lectivo")
    verificar_turma(db, ctx, m.turma_id)
    return _pdf(docs.boletim(db, a, p), f"boletim-{a.numero_processo}")


@router.get("/turmas/{tid}/pauta.pdf", responses=R.PDF, summary="Pauta da turma num período")
def pauta_pdf(tid: int, periodo_id: int, ctx: Contexto = Depends(exige("pedagogico")), db: Session = Depends(get_db)):
    t = obter(db, Turma, tid, ctx.estabelecimento_id, "Turma")
    verificar_turma(db, ctx, tid)
    return _pdf(docs.pauta(db, t, _periodo(db, periodo_id, ctx.estabelecimento_id)), f"pauta-{t.designacao}")


@router.get("/matriculas/{mid}/declaracao.pdf", responses=R.PDF, summary="Declaração de matrícula")
def declaracao_pdf(mid: int, ctx: Contexto = Depends(exige("cadastro")), db: Session = Depends(get_db)):
    m = obter(db, Matricula, mid, ctx.estabelecimento_id, "Matrícula")
    auditar(db, ctx, "matricula", mid, "export", {"documento": "declaracao"})
    db.commit()
    return _pdf(docs.declaracao(db, m), f"declaracao-{mid}")
