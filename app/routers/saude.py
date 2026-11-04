"""Saúde escolar: ocorrências (com registo cronológico e contactos à família), instruções de medicação dos encarregados
e administração. Acesso restrito ao módulo "saude" (Direcção e responsável de saúde; coordenação consulta e regista,
professores só registam); cada consulta fica na auditoria."""
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import respostas as R
from app.common import as_dict, obter
from app.database import get_db
from app.models import (
    Alerta, Aluno, MedicacaoAdministracao, MedicacaoInstrucao, OcorrenciaSaude, OcorrenciaSaudeNota, Utilizador,
)
from app.schemas import (
    AdministracaoIn, DecisaoMedicacaoIn, EncerrarSaudeIn, MedicacaoIn, NotaSaudeIn, OcorrenciaSaudeIn,
    OcorrenciaSaudeUpd,
)
from app.security import Contexto, auditar, exige
from app.services import secretaria

router = APIRouter(prefix="/api/saude", tags=["Saúde escolar"])
ver, criar, editar = exige("saude"), exige("saude", "criar"), exige("saude", "editar")
DESFECHOS = {"ficou_na_escola": "ficou na escola", "recolhido": "foi recolhido pela família", "encaminhado": "foi encaminhado"}


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def _nome(db: Session, uid: int | None) -> str | None:
    u = uid and db.get(Utilizador, uid)
    return u.nome if u else None


def _ocorrencia(db: Session, o: OcorrenciaSaude) -> dict:
    notas = db.scalars(select(OcorrenciaSaudeNota).where(OcorrenciaSaudeNota.ocorrencia_id == o.id)
                       .order_by(OcorrenciaSaudeNota.em, OcorrenciaSaudeNota.id))
    return {**as_dict(o), "aluno": db.get(Aluno, o.aluno_id).nome_completo, "registada_por_nome": _nome(db, o.registada_por),
            "notas": [{**as_dict(n), "por_nome": _nome(db, n.por)} for n in notas]}


def _nota(db: Session, o: OcorrenciaSaude, tipo: str, texto: str, por: int, **extra) -> None:
    db.add(OcorrenciaSaudeNota(ocorrencia_id=o.id, tipo=tipo, texto=texto, por=por, **extra))


# ---------------------------------------------------------------- ocorrências
@router.get("/ocorrencias", response_model=list[R.OcorrenciaSaudeResumo],
            summary="Ocorrências de saúde (estado=aberta: em seguimento)")
def ocorrencias(estado: str | None = None, aluno_id: int | None = None, desde: date | None = None,
                ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    stmt = (select(OcorrenciaSaude, Aluno.nome_completo).join(Aluno, Aluno.id == OcorrenciaSaude.aluno_id)
            .where(OcorrenciaSaude.estabelecimento_id == ctx.estabelecimento_id))
    if estado:
        stmt = stmt.where(OcorrenciaSaude.estado == estado)
    if aluno_id:
        stmt = stmt.where(OcorrenciaSaude.aluno_id == aluno_id)
    if desde:
        stmt = stmt.where(OcorrenciaSaude.data_hora >= datetime.combine(desde, datetime.min.time()))
    linhas = [{**as_dict(o), "aluno": nome} for o, nome in db.execute(stmt.order_by(OcorrenciaSaude.data_hora.desc()).limit(500))]
    auditar(db, ctx, "ocorrencia_saude", None, "view", {"lista": len(linhas), "aluno_id": aluno_id})
    db.commit()
    return linhas


@router.post("/ocorrencias", response_model=R.OcorrenciaSaudeDetalhe, status_code=201,
             summary="Regista um incidente de saúde (a Direcção/saúde é avisada)")
def criar_ocorrencia(dados: OcorrenciaSaudeIn, ctx: Contexto = Depends(criar), db: Session = Depends(get_db)):
    a = obter(db, Aluno, dados.aluno_id, ctx.estabelecimento_id, "Aluno")
    o = OcorrenciaSaude(estabelecimento_id=ctx.estabelecimento_id, registada_por=ctx.id, **dados.model_dump())
    db.add(o)
    db.flush()
    db.add(Alerta(estabelecimento_id=ctx.estabelecimento_id, tipo="saude", aluno_id=a.id, gravidade="media",
                  dados={"ocorrencia_saude_id": o.id}, mensagem=f"Ocorrência de saúde: {a.nome_completo} — {dados.descricao}"))
    auditar(db, ctx, "ocorrencia_saude", o.id, "insert", {"aluno_id": a.id})
    db.commit()
    return _ocorrencia(db, o)


@router.get("/ocorrencias/{oid}", response_model=R.OcorrenciaSaudeDetalhe, summary="Ocorrência com o registo cronológico")
def ver_ocorrencia(oid: int, ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    o = obter(db, OcorrenciaSaude, oid, ctx.estabelecimento_id, "Ocorrência de saúde")
    auditar(db, ctx, "ocorrencia_saude", oid, "view")
    db.commit()
    return _ocorrencia(db, o)


@router.patch("/ocorrencias/{oid}", response_model=R.OcorrenciaSaudeDetalhe,
              summary="Corrige uma ocorrência aberta (cada alteração fica no registo); encerrada não se altera")
def editar_ocorrencia(oid: int, dados: OcorrenciaSaudeUpd, ctx: Contexto = Depends(editar), db: Session = Depends(get_db)):
    o = obter(db, OcorrenciaSaude, oid, ctx.estabelecimento_id, "Ocorrência de saúde")
    if o.estado == "encerrada":
        raise HTTPException(409, "A ocorrência está encerrada: acrescente uma nota em vez de a alterar")
    antes, mudancas = {}, dados.model_dump(exclude_unset=True)
    for k, v in mudancas.items():
        antes[k] = getattr(o, k)
        setattr(o, k, v)
    _nota(db, o, "alteracao", "Alterado: " + ", ".join(f"{k} ({antes[k] or '—'} → {v})" for k, v in mudancas.items()), ctx.id)
    auditar(db, ctx, "ocorrencia_saude", oid, "update", {k: str(v) for k, v in mudancas.items()},
            {k: str(v) for k, v in antes.items()})
    db.commit()
    return _ocorrencia(db, o)


@router.post("/ocorrencias/{oid}/notas", response_model=R.OcorrenciaSaudeDetalhe, status_code=201,
             summary="Acrescenta uma nota ou o contacto com a família (quem, canal, orientação)")
def nota(oid: int, dados: NotaSaudeIn, ctx: Contexto = Depends(criar), db: Session = Depends(get_db)):
    o = obter(db, OcorrenciaSaude, oid, ctx.estabelecimento_id, "Ocorrência de saúde")
    _nota(db, o, dados.tipo, dados.texto, ctx.id, canal=dados.canal, contactado=dados.contactado, orientacao=dados.orientacao)
    db.commit()
    return _ocorrencia(db, o)


@router.post("/ocorrencias/{oid}/encerrar", response_model=R.OcorrenciaSaudeDetalhe,
             summary="Encerra com o desfecho; a partir daqui só se acrescentam notas. Partilhada: a família é avisada")
def encerrar(oid: int, dados: EncerrarSaudeIn, ctx: Contexto = Depends(editar), db: Session = Depends(get_db)):
    o = obter(db, OcorrenciaSaude, oid, ctx.estabelecimento_id, "Ocorrência de saúde")
    if o.estado == "encerrada":
        raise HTTPException(409, "A ocorrência já está encerrada")
    o.estado, o.desfecho, o.encerrada_por, o.encerrada_em = "encerrada", dados.desfecho, ctx.id, _agora()
    _nota(db, o, "nota", dados.nota or f"Encerrada: o aluno {DESFECHOS[dados.desfecho]}", ctx.id)
    for a in db.scalars(select(Alerta).where(Alerta.tipo == "saude", Alerta.estado.in_(("novo", "visto")),
                                             Alerta.estabelecimento_id == ctx.estabelecimento_id)):
        if (a.dados or {}).get("ocorrencia_saude_id") == o.id:
            a.estado, a.tratado_por = "tratado", ctx.id
    if o.partilhada:
        aluno = db.get(Aluno, o.aluno_id)
        secretaria.avisar_encarregados(db, aluno, "Saúde", f"Registámos uma ocorrência de saúde de {aluno.nome_completo}: "
                                       f"{o.descricao} O aluno {DESFECHOS[dados.desfecho]}. Veja o relatório no portal.")
    auditar(db, ctx, "ocorrencia_saude", oid, "update", {"estado": "encerrada", "desfecho": dados.desfecho})
    db.commit()
    return _ocorrencia(db, o)


# ---------------------------------------------------------------- medicação
def _instrucao(db: Session, m: MedicacaoInstrucao) -> dict:
    adm = db.scalars(select(MedicacaoAdministracao).where(MedicacaoAdministracao.instrucao_id == m.id)
                     .order_by(MedicacaoAdministracao.administrada_em.desc()))
    return {**as_dict(m), "aluno": db.get(Aluno, m.aluno_id).nome_completo,
            "administracoes": [{**as_dict(x), "por_nome": _nome(db, x.por)} for x in adm]}


@router.get("/medicacao", response_model=list[R.MedicacaoDetalhe],
            summary="Instruções de medicação (estado=pendente: por autorizar; activas=true: em vigor hoje)")
def medicacao(estado: str | None = None, aluno_id: int | None = None, activas: bool = False,
              ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    stmt = select(MedicacaoInstrucao).where(MedicacaoInstrucao.estabelecimento_id == ctx.estabelecimento_id)
    if estado:
        stmt = stmt.where(MedicacaoInstrucao.estado == estado)
    if aluno_id:
        stmt = stmt.where(MedicacaoInstrucao.aluno_id == aluno_id)
    if activas:
        hoje = date.today()
        stmt = stmt.where(MedicacaoInstrucao.estado == "autorizada", MedicacaoInstrucao.data_inicio <= hoje,
                          (MedicacaoInstrucao.data_fim.is_(None)) | (MedicacaoInstrucao.data_fim >= hoje))
    linhas = [_instrucao(db, m) for m in db.scalars(stmt.order_by(MedicacaoInstrucao.criado_em.desc()).limit(500))]
    auditar(db, ctx, "medicacao_instrucao", None, "view", {"lista": len(linhas)})
    db.commit()
    return linhas


@router.post("/medicacao", response_model=R.MedicacaoDetalhe, status_code=201,
             summary="Regista uma instrução entregue em papel (fica por autorizar)")
def criar_medicacao(dados: MedicacaoIn, ctx: Contexto = Depends(criar), db: Session = Depends(get_db)):
    obter(db, Aluno, dados.aluno_id, ctx.estabelecimento_id, "Aluno")
    m = MedicacaoInstrucao(estabelecimento_id=ctx.estabelecimento_id, **dados.model_dump())
    db.add(m)
    db.commit()
    return _instrucao(db, m)


@router.post("/medicacao/{mid}/decidir", response_model=R.MedicacaoDetalhe,
             summary="Autoriza ou recusa (com o motivo) segundo o protocolo da escola; a família é avisada")
def decidir_medicacao(mid: int, dados: DecisaoMedicacaoIn, ctx: Contexto = Depends(editar), db: Session = Depends(get_db)):
    m = obter(db, MedicacaoInstrucao, mid, ctx.estabelecimento_id, "Instrução de medicação")
    if m.estado != "pendente":
        raise HTTPException(409, f"A instrução já está {m.estado}")
    if not dados.autorizar and not (dados.motivo or "").strip():
        raise HTTPException(422, "Indique o motivo da recusa")
    if dados.autorizar and m.encarregado_id and not m.confirmada_pela_familia:
        raise HTTPException(409, "A família ainda não confirmou a instrução por escrito")
    m.estado, m.motivo = ("autorizada" if dados.autorizar else "recusada"), dados.motivo
    m.validada_por, m.validada_em = ctx.id, _agora()
    aluno = db.get(Aluno, m.aluno_id)
    secretaria.avisar_encarregados(db, aluno, "Medicação",
                                   f"A instrução de medicação de {aluno.nome_completo} ({m.medicamento}) foi "
                                   f"{'autorizada' if dados.autorizar else 'recusada'}." + (f" {dados.motivo}" if dados.motivo else ""))
    auditar(db, ctx, "medicacao_instrucao", mid, "update", {"estado": m.estado})
    db.commit()
    return _instrucao(db, m)


@router.post("/medicacao/{mid}/administracoes", response_model=R.MedicacaoDetalhe, status_code=201,
             summary="Regista a administração (ou que não foi tomada): quem, quando, dose")
def administrar(mid: int, dados: AdministracaoIn, ctx: Contexto = Depends(criar), db: Session = Depends(get_db)):
    m = obter(db, MedicacaoInstrucao, mid, ctx.estabelecimento_id, "Instrução de medicação")
    hoje = date.today()
    if m.estado != "autorizada" or m.data_inicio > hoje or (m.data_fim and m.data_fim < hoje):
        raise HTTPException(409, "Só se administra uma instrução autorizada e dentro das datas")
    db.add(MedicacaoAdministracao(instrucao_id=m.id, administrada_em=dados.administrada_em or _agora(),
                                  dose=dados.dose or m.dose, administrada=dados.administrada,
                                  observacao=dados.observacao, por=ctx.id))
    auditar(db, ctx, "medicacao_instrucao", mid, "insert", {"administrada": dados.administrada})
    db.commit()
    return _instrucao(db, m)


@router.post("/medicacao/{mid}/terminar", response_model=R.MedicacaoDetalhe, summary="Termina uma instrução autorizada")
def terminar(mid: int, ctx: Contexto = Depends(editar), db: Session = Depends(get_db)):
    m = obter(db, MedicacaoInstrucao, mid, ctx.estabelecimento_id, "Instrução de medicação")
    if m.estado != "autorizada":
        raise HTTPException(409, "Só se termina uma instrução autorizada")
    m.estado, m.data_fim = "terminada", min(m.data_fim or date.today(), date.today())
    db.commit()
    return _instrucao(db, m)


# ---------------------------------------------------------------- painel
@router.get("/painel", response_model=R.PainelSaude,
            summary="Ocorrências abertas e de hoje, medicação por autorizar e a dar hoje, alunos com alergias")
def painel(ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    est, hoje = ctx.estabelecimento_id, date.today()
    contar = lambda *w: db.scalar(select(func.count()).select_from(w[0]).where(*w[1:])) or 0  # noqa: E731
    activas = db.scalars(select(MedicacaoInstrucao).where(
        MedicacaoInstrucao.estabelecimento_id == est, MedicacaoInstrucao.estado == "autorizada",
        MedicacaoInstrucao.data_inicio <= hoje,
        (MedicacaoInstrucao.data_fim.is_(None)) | (MedicacaoInstrucao.data_fim >= hoje))).all()
    dadas_hoje = {i for (i,) in db.execute(select(MedicacaoAdministracao.instrucao_id).where(
        MedicacaoAdministracao.instrucao_id.in_([m.id for m in activas]),
        func.date(MedicacaoAdministracao.administrada_em) == hoje))}
    return {
        "ocorrencias_abertas": contar(OcorrenciaSaude, OcorrenciaSaude.estabelecimento_id == est, OcorrenciaSaude.estado == "aberta"),
        "ocorrencias_hoje": contar(OcorrenciaSaude, OcorrenciaSaude.estabelecimento_id == est,
                                   func.date(OcorrenciaSaude.data_hora) == hoje),
        "medicacao_por_autorizar": contar(MedicacaoInstrucao, MedicacaoInstrucao.estabelecimento_id == est,
                                          MedicacaoInstrucao.estado == "pendente"),
        "medicacao_activa": len(activas),
        "medicacao_por_dar_hoje": sum(1 for m in activas if m.id not in dadas_hoje),
        "alunos_com_alergias": contar(Aluno, Aluno.estabelecimento_id == est, Aluno.estado == "activo",
                                      Aluno.alergias.is_not(None), Aluno.alergias != "",
                                      func.lower(Aluno.alergias).not_in(("nenhuma", "não", "nao"))),
    }
