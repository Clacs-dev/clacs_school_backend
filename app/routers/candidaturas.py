"""Candidaturas de alunos novos: da pré-inscrição (Google Forms) à matrícula e às boas-vindas.

Do lado da escola (/api/candidaturas) cada etapa é um POST com o nome do passo; a etapa só é aceite no estado certo
e pelo papel certo (coordenação, Direcção, secretaria). Do lado da família (/api/publico/candidaturas) não há conta:
o formulário Google envia com um segredo partilhado e a formalização da matrícula usa o link com token.
"""
import csv
import hmac
import io
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app import respostas as R
from app.common import as_dict, obter
from app.config import get_settings
from app.database import get_db
from app.models import Candidatura, NivelEnsino
from app.schemas import (
    AvaliacaoCandidaturaIn, BoasVindasIn, CandidaturaIn, CandidaturaUpd, ComunicarVagaIn, CorreccaoIn, FormalizacaoIn,
    GoogleFormsIn, MatricularCandidaturaIn, ParecerIn, ReuniaoIn,
)
from app.security import Contexto, auditar, exige
from app.services import candidaturas as svc

router = APIRouter(prefix="/api/candidaturas", tags=["Candidaturas e admissões"])
publico = APIRouter(prefix="/api/publico/candidaturas", tags=["Candidaturas e admissões"])
SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "google_forms_apps_script.js"


def _obter(db: Session, ctx: Contexto, cid: int) -> Candidatura:
    return obter(db, Candidatura, cid, ctx.estabelecimento_id, "Candidatura")


def _fim(db: Session, ctx: Contexto, c: Candidatura, passo: str) -> dict:
    auditar(db, ctx, "candidatura", c.id, "update", {"passo": passo, "estado": c.estado})
    db.commit()
    return svc.detalhe(db, c)


# ---------------------------------------------------------------- consulta
@router.get("", response_model=list[R.CandidaturaResumo],
            summary="Candidaturas (filtros: etapa, nível, classe, texto; meus_niveis = só os níveis que coordeno)")
def listar(estado: str | None = None, nivel_id: int | None = None, classe_id: int | None = None, q: str | None = None,
           meus_niveis: bool = False, ctx: Contexto = Depends(exige("candidaturas")), db: Session = Depends(get_db)):
    stmt = select(Candidatura).where(Candidatura.estabelecimento_id == ctx.estabelecimento_id)
    if estado == "em_curso":
        stmt = stmt.where(Candidatura.estado.not_in(("concluida", "rejeitada", "cancelada")))
    elif estado:
        stmt = stmt.where(Candidatura.estado == estado)
    if nivel_id:
        stmt = stmt.where(Candidatura.nivel_id == nivel_id)
    if classe_id:
        stmt = stmt.where(Candidatura.classe_id == classe_id)
    if meus_niveis:
        stmt = stmt.where(Candidatura.nivel_id.in_(select(NivelEnsino.id).where(NivelEnsino.coordenador_id == ctx.id)))
    if q:
        termo = f"%{q.lower()}%"
        stmt = stmt.where(or_(func.lower(Candidatura.nome_aluno).like(termo),
                              func.lower(Candidatura.nome_encarregado).like(termo),
                              func.lower(Candidatura.email).like(termo), Candidatura.telefone.like(termo)))
    return [svc.resumo(db, c) for c in db.scalars(stmt.order_by(Candidatura.criado_em.desc()).limit(500))]


@router.get("/resumo", response_model=R.ResumoCandidaturas, summary="Quantas candidaturas há em cada etapa")
def resumo(ctx: Contexto = Depends(exige("candidaturas")), db: Session = Depends(get_db)):
    contagem = dict(db.execute(select(Candidatura.estado, func.count()).where(
        Candidatura.estabelecimento_id == ctx.estabelecimento_id).group_by(Candidatura.estado)).all())
    return {"total": sum(contagem.values()), "por_estado": {e: contagem.get(e, 0) for e in svc.NOMES_ESTADO}}


@router.get("/google-forms", response_model=R.GoogleFormsConfig,
            summary="Como ligar o Google Forms: endpoint, perguntas reconhecidas e o Apps Script pronto a colar")
def google_forms(request: Request, ctx: Contexto = Depends(exige("candidaturas", "editar"))):
    s = get_settings()
    url = str(request.base_url).rstrip("/") + publico.prefix + "/google-forms"
    # O segredo só aparece ao administrador; os restantes vêem o script com o lugar para o colar.
    segredo = s.google_forms_segredo if "admin" in ctx.perfis and s.google_forms_segredo else "COLE_AQUI_O_SEGREDO"
    script = SCRIPT.read_text(encoding="utf-8").replace("{{URL}}", url).replace("{{SEGREDO}}", segredo)
    return {"endpoint": publico.prefix + "/google-forms", "estabelecimento_id": s.google_forms_estabelecimento_id,
            "segredo_configurado": bool(s.google_forms_segredo), "perguntas": svc.TITULOS_SUGERIDOS, "script": script}


@router.get("/{cid}", response_model=R.CandidaturaDetalhe,
            summary="Candidatura com documentos, vagas da classe, coordenador e o que falta à família")
def ver(cid: int, ctx: Contexto = Depends(exige("candidaturas")), db: Session = Depends(get_db)):
    return svc.detalhe(db, _obter(db, ctx, cid))


# ---------------------------------------------------------------- 1-2. pré-inscrição
@router.post("", response_model=R.CandidaturaDetalhe, status_code=201,
             summary="Regista uma pré-inscrição (ex.: entregue em papel); segue para a coordenação do nível")
def criar(dados: CandidaturaIn, ctx: Contexto = Depends(exige("candidaturas", "criar")), db: Session = Depends(get_db)):
    c = svc.criar(db, ctx.estabelecimento_id, dados.model_dump(), "secretaria", ctx.utilizador)
    auditar(db, ctx, "candidatura", c.id, "insert", {"nome_aluno": c.nome_aluno})
    db.commit()
    return svc.detalhe(db, c)


@router.post("/importar", response_model=R.ImportacaoCandidaturas,
             summary="Importa o CSV das respostas do Google Forms (Respostas → Transferir respostas .csv)")
async def importar(ficheiro: UploadFile = File(..., description="CSV exportado do Google Forms ou da folha de respostas"),
                   ctx: Contexto = Depends(exige("candidaturas", "criar")), db: Session = Depends(get_db)):
    conteudo = await ficheiro.read()
    if len(conteudo) > get_settings().upload_max_mb * 1024 * 1024:
        raise HTTPException(413, "Ficheiro demasiado grande")
    texto = conteudo.decode("utf-8-sig", errors="replace")
    try:
        dialecto = csv.Sniffer().sniff(texto[:4096], delimiters=",;\t")
    except csv.Error:
        dialecto = csv.excel
    linhas = list(csv.DictReader(io.StringIO(texto), dialect=dialecto))
    if not linhas:
        raise HTTPException(422, "O ficheiro não tem respostas")
    criadas = duplicadas = 0
    erros = []
    for n, linha in enumerate(linhas, start=2):  # linha 1 = títulos das perguntas
        respostas = {k: v for k, v in linha.items() if k and v and "carimbo" not in svc.normalizar(k)
                     and "timestamp" not in k.lower()}
        try:
            with db.begin_nested():
                _, repetida, _ = svc.receber_formulario(db, ctx.estabelecimento_id, respostas, None, origem="importacao")
            duplicadas += repetida
            criadas += not repetida
        except HTTPException as erro:
            erros.append({"linha": n, "mensagem": erro.detail})
    auditar(db, ctx, "candidatura", None, "insert", {"importacao": ficheiro.filename, "criadas": criadas})
    db.commit()
    return {"criadas": criadas, "duplicadas": duplicadas, "erros": erros}


@router.patch("/{cid}", response_model=R.CandidaturaDetalhe, summary="Corrige os dados da pré-inscrição (ex.: a classe)")
def actualizar(cid: int, dados: CandidaturaUpd, ctx: Contexto = Depends(exige("candidaturas", "editar")),
               db: Session = Depends(get_db)):
    c = _obter(db, ctx, cid)
    svc.actualizar(db, c, dados.model_dump(exclude_unset=True))
    return _fim(db, ctx, c, "actualizar")


# ---------------------------------------------------------------- 3-4. coordenação
@router.post("/{cid}/reuniao", response_model=R.CandidaturaDetalhe,
             summary="Coordenação: convoca a família para a reunião obrigatória (ou remarca)")
def reuniao(cid: int, dados: ReuniaoIn, ctx: Contexto = Depends(exige("candidaturas", "editar")),
            db: Session = Depends(get_db)):
    c = _obter(db, ctx, cid)
    svc.exigir_papel(db, ctx, c, "coordenacao")
    svc.marcar_reuniao(db, c, ctx.utilizador, dados.reuniao_em, dados.reuniao_local, dados.mensagem)
    return _fim(db, ctx, c, "reuniao")


@router.post("/{cid}/avaliacao", response_model=R.CandidaturaDetalhe,
             summary="Coordenação: regista a reunião e, se houve, a entrevista ou avaliação do aluno")
def avaliacao(cid: int, dados: AvaliacaoCandidaturaIn, ctx: Contexto = Depends(exige("candidaturas", "editar")),
              db: Session = Depends(get_db)):
    c = _obter(db, ctx, cid)
    svc.exigir_papel(db, ctx, c, "coordenacao")
    svc.registar_reuniao(db, c, ctx.utilizador, dados.avaliacao)
    return _fim(db, ctx, c, "avaliacao")


@router.post("/{cid}/validar", response_model=R.CandidaturaDetalhe,
             summary="Coordenação: valida (exige a reunião feita e vaga na classe); segue para a Direcção")
def validar(cid: int, dados: ParecerIn, ctx: Contexto = Depends(exige("candidaturas", "editar")),
            db: Session = Depends(get_db)):
    c = _obter(db, ctx, cid)
    svc.exigir_papel(db, ctx, c, "coordenacao")
    svc.validar(db, c, ctx.utilizador, dados.parecer)
    return _fim(db, ctx, c, "validar")


@router.post("/{cid}/rejeitar", response_model=R.CandidaturaDetalhe,
             summary="Coordenação: rejeita com o motivo (a família é avisada)")
def rejeitar(cid: int, dados: ParecerIn, ctx: Contexto = Depends(exige("candidaturas", "editar")),
             db: Session = Depends(get_db)):
    c = _obter(db, ctx, cid)
    svc.exigir_papel(db, ctx, c, "coordenacao")
    svc.rejeitar(db, c, ctx.utilizador, dados.parecer)
    return _fim(db, ctx, c, "rejeitar")


# ---------------------------------------------------------------- 5-6. Direcção e secretaria
@router.post("/{cid}/autorizar", response_model=R.CandidaturaDetalhe,
             summary="Direcção: autoriza a admissão e informa os serviços administrativos")
def autorizar(cid: int, dados: ParecerIn, ctx: Contexto = Depends(exige("candidaturas", "editar")),
              db: Session = Depends(get_db)):
    c = _obter(db, ctx, cid)
    svc.exigir_papel(db, ctx, c, "direccao")
    svc.autorizar(db, c, ctx.utilizador, dados.parecer)
    return _fim(db, ctx, c, "autorizar")


@router.post("/{cid}/comunicar", response_model=R.CandidaturaDetalhe,
             summary="Secretaria: comunica a vaga e os passos à família, com o link para formalizar (ou reenvia)")
def comunicar(cid: int, dados: ComunicarVagaIn, ctx: Contexto = Depends(exige("candidaturas", "editar")),
              db: Session = Depends(get_db)):
    c = _obter(db, ctx, cid)
    svc.exigir_papel(db, ctx, c, "secretaria")
    svc.comunicar_vaga(db, c, ctx.utilizador, dados.mensagem)
    return _fim(db, ctx, c, "comunicar")


# ---------------------------------------------------------------- 8-10. secretaria
@router.post("/{cid}/correccao", response_model=R.CandidaturaDetalhe,
             summary="Secretaria: devolve à família para corrigir dados ou documentos")
def correccao(cid: int, dados: CorreccaoIn, ctx: Contexto = Depends(exige("candidaturas", "editar")),
              db: Session = Depends(get_db)):
    c = _obter(db, ctx, cid)
    svc.exigir_papel(db, ctx, c, "secretaria")
    svc.pedir_correccao(db, c, ctx.utilizador, dados.correccao)
    return _fim(db, ctx, c, "correccao")


@router.post("/{cid}/matricular", response_model=R.CandidaturaDetalhe,
             summary="Secretaria: valida os dados e cria aluno, encarregado, documentos e matrícula de uma vez")
def matricular(cid: int, dados: MatricularCandidaturaIn, ctx: Contexto = Depends(exige("candidaturas", "editar")),
               db: Session = Depends(get_db)):
    c = _obter(db, ctx, cid)
    svc.exigir_papel(db, ctx, c, "secretaria")
    svc.matricular(db, c, ctx.utilizador, dados.turma_id)
    return _fim(db, ctx, c, "matricular")


@router.post("/{cid}/boas-vindas", response_model=R.CandidaturaDetalhe,
             summary="Secretaria: welcome kit, documentação e integração; envia as boas-vindas e conclui")
def boas_vindas(cid: int, dados: BoasVindasIn, ctx: Contexto = Depends(exige("candidaturas", "editar")),
                db: Session = Depends(get_db)):
    c = _obter(db, ctx, cid)
    svc.exigir_papel(db, ctx, c, "secretaria")
    svc.boas_vindas(db, c, ctx.utilizador, dados.model_dump())
    return _fim(db, ctx, c, "boas_vindas")


@router.post("/{cid}/cancelar", response_model=R.CandidaturaDetalhe, summary="Desistência da família ou duplicado")
def cancelar(cid: int, dados: ParecerIn, ctx: Contexto = Depends(exige("candidaturas", "editar")),
             db: Session = Depends(get_db)):
    c = _obter(db, ctx, cid)
    svc.cancelar(db, c, ctx.utilizador, dados.parecer)
    return _fim(db, ctx, c, "cancelar")


# ---------------------------------------------------------------- público: Google Forms e formalização pela família
@publico.post("/google-forms", response_model=R.RespostaFormulario, status_code=201,
              summary="Recebe uma resposta do Google Forms (Apps Script com o cabeçalho X-Clacs-Segredo)")
def receber_google_forms(dados: GoogleFormsIn, x_clacs_segredo: str = Header(""), db: Session = Depends(get_db)):
    s = get_settings()
    if not s.google_forms_segredo or not hmac.compare_digest(x_clacs_segredo, s.google_forms_segredo):
        raise HTTPException(401, "Segredo do formulário inválido")
    c, duplicada, avisos = svc.receber_formulario(db, s.google_forms_estabelecimento_id, dict(dados.respostas),
                                                  dados.id_resposta)
    db.commit()
    return {"candidatura_id": c.id, "estado": c.estado, "duplicada": duplicada, "avisos": avisos}


@publico.get("/{token}", response_model=R.CandidaturaPublica,
             summary="Família: estado da candidatura, dados já preenchidos e documentos pedidos")
def ver_publica(token: str, db: Session = Depends(get_db)):
    return svc.publica(db, svc.por_token(db, token))


@publico.put("/{token}", response_model=R.CandidaturaPublica, summary="Família: grava os dados da matrícula")
def gravar_publica(token: str, dados: FormalizacaoIn, db: Session = Depends(get_db)):
    c = svc.por_token(db, token)
    svc.gravar_dados(c, dados.model_dump(mode="json", exclude_unset=True))
    db.commit()
    return svc.publica(db, c)


@publico.post("/{token}/documentos", response_model=R.CandidaturaPublica, status_code=201,
              summary="Família: carrega um documento (PDF ou imagem)")
async def documento_publico(token: str, tipo: str = Form(..., examples=["cedula_bi_aluno"]),
                            ficheiro: UploadFile = File(..., description="PDF, JPG, PNG ou WEBP, até 10 MB"),
                            db: Session = Depends(get_db)):
    c = svc.por_token(db, token)
    svc.anexar(db, c, tipo, await ficheiro.read(), ficheiro.content_type, ficheiro.filename)
    db.commit()
    return svc.publica(db, c)


@publico.delete("/{token}/documentos/{did}", response_model=R.CandidaturaPublica,
                summary="Família: retira um documento carregado por engano")
def apagar_documento_publico(token: str, did: int, db: Session = Depends(get_db)):
    c = svc.por_token(db, token)
    svc.remover_documento(db, c, did)
    db.commit()
    return svc.publica(db, c)


@publico.post("/{token}/submeter", response_model=R.CandidaturaPublica,
              summary="Família: entrega os dados e os documentos à secretaria")
def submeter_publica(token: str, db: Session = Depends(get_db)):
    c = svc.por_token(db, token)
    svc.submeter(db, c)
    db.commit()
    return svc.publica(db, c)
