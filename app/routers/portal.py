"""Portal do encarregado: cada encarregado vê só os seus educandos, as suas cobranças e as suas mensagens."""
import json
import logging
from datetime import date
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app import armazenamento
from app import respostas as R
from app.common import as_dict
from app.database import get_db
from app.integracoes import ErroIntegracao
from app.integracoes.pagamentos import gateway, validar_webhook
from app.models import (
    Aluno, AlunoEncarregado, AnoLectivo, Avaliacao, Disciplina, DispositivoPush, Encarregado, Matricula, Mensagem,
    MetodoPagamento, Nota, Pagamento, Perfil, PeriodoLectivo, PreferenciaContacto, TipoAvaliacao, Turma, Utilizador,
    UtilizadorPerfil,
)
from app.schemas import Canal, Entrada
from app.security import Contexto, auditar, contexto_actual, exige, hash_senha
from app.services import documentos as docs
from app.services.alertas import comportamento_actual
from app.services.financeiro import cobrancas_detalhe, mudar_estado_pagamento, registar_pagamento
from app.services.pedagogico import media_geral, total_faltas

router = APIRouter(prefix="/api/portal", tags=["Portal do encarregado"])
extra = APIRouter(prefix="/api", tags=["Portal do encarregado"])
log = logging.getLogger("clacs.portal")


class ReferenciaIn(Entrada):
    cobranca_ids: list[int] = Field(min_length=1)


class PreferenciaPortalIn(Entrada):
    canal: Canal
    activo: bool


class ContaIn(Entrada):
    email: str
    senha: str = Field(min_length=8)


def encarregado_actual(ctx: Contexto = Depends(contexto_actual), db: Session = Depends(get_db)) -> Encarregado:
    eid = ctx.utilizador.encarregado_id
    if "encarregado" not in ctx.perfis or not eid:
        raise HTTPException(403, "Esta área é só para encarregados de educação")
    return db.get(Encarregado, eid)


def _educandos(db: Session, ee: Encarregado) -> list[int]:
    return list(db.scalars(select(AlunoEncarregado.aluno_id).where(AlunoEncarregado.encarregado_id == ee.id)))


def _educando(db: Session, ee: Encarregado, aid: int) -> Aluno:
    if aid not in _educandos(db, ee):
        raise HTTPException(404, "Educando não encontrado")
    return db.get(Aluno, aid)


def _minhas_cobrancas(db: Session, ee: Encarregado, estado: str | None = None) -> list[dict]:
    linhas = []
    for aid in _educandos(db, ee):
        linhas += cobrancas_detalhe(db, ee.estabelecimento_id, aluno_id=aid, estado=estado)
    return sorted(linhas, key=lambda x: (x["vencimento"], x["aluno"]))


@router.get("/eu", response_model=R.PortalEu, summary="Dados do encarregado e resumo de cada educando")
def eu(ee: Encarregado = Depends(encarregado_actual), db: Session = Depends(get_db)):
    educandos = []
    for aid in _educandos(db, ee):
        a = db.get(Aluno, aid)
        cob = cobrancas_detalhe(db, ee.estabelecimento_id, aluno_id=aid, estado="divida")
        educandos.append({"id": a.id, "nome": a.nome_completo, "numero_processo": a.numero_processo,
                          "em_divida": sum(c["valor_em_aberto"] for c in cob),
                          "em_atraso": sum(c["valor_em_aberto"] for c in cob if c["dias_atraso"] > 0),
                          "turma": cob[0]["turma"] if cob else None})
    prefs = {p.canal: p.activo for p in db.scalars(select(PreferenciaContacto).where(PreferenciaContacto.encarregado_id == ee.id))}
    return {"encarregado": {k: v for k, v in as_dict(ee).items() if k in ("id", "nome_completo", "telefone", "whatsapp", "email", "nif")},
            "educandos": educandos, "preferencias": prefs}


@router.get("/educandos/{aid}", response_model=R.EducandoPortal, summary="Situação do educando: financeiro e pedagógico")
def educando(aid: int, ee: Encarregado = Depends(encarregado_actual), db: Session = Depends(get_db)):
    a = _educando(db, ee, aid)
    m = db.scalar(select(Matricula).join(AnoLectivo, AnoLectivo.id == Matricula.ano_id)
                  .where(Matricula.aluno_id == aid, Matricula.estado == "activa", AnoLectivo.estado == "activo"))
    turma = db.get(Turma, m.turma_id) if m else None
    cob = cobrancas_detalhe(db, ee.estabelecimento_id, aluno_id=aid)
    return {"aluno": {"id": a.id, "nome": a.nome_completo, "numero_processo": a.numero_processo, "foto_url": a.foto_url},
            "turma": turma.designacao if turma else None,
            "cobrancas": cob, "em_divida": sum(c["valor_em_aberto"] for c in cob if c["estado"] != "paga"),
            "media": media_geral(db, m) if m else None, "faltas": total_faltas(db, aid, m.turma_id if m else None),
            "comportamento": comportamento_actual(db, aid)}


@router.get("/educandos/{aid}/notas", response_model=list[R.NotaPortal], summary="Notas lançadas do educando")
def notas(aid: int, ee: Encarregado = Depends(encarregado_actual), db: Session = Depends(get_db)):
    _educando(db, ee, aid)
    rows = db.execute(select(Nota, Avaliacao, Disciplina.nome, TipoAvaliacao.designacao, PeriodoLectivo.designacao)
                      .join(Avaliacao, Avaliacao.id == Nota.avaliacao_id)
                      .join(Disciplina, Disciplina.id == Avaliacao.disciplina_id)
                      .join(TipoAvaliacao, TipoAvaliacao.id == Avaliacao.tipo_id)
                      .join(PeriodoLectivo, PeriodoLectivo.id == Avaliacao.periodo_id)
                      .where(Nota.aluno_id == aid).order_by(Avaliacao.data.desc()))
    return [{"data": av.data, "periodo": per, "disciplina": d, "tipo": t,
             "valor": float(n.valor) if n.valor is not None else None, "observacao": n.observacao}
            for n, av, d, t, per in rows]


@router.get("/educandos/{aid}/boletim.pdf", responses=R.PDF)
def boletim(aid: int, periodo_id: int, ee: Encarregado = Depends(encarregado_actual), db: Session = Depends(get_db)):
    a = _educando(db, ee, aid)
    p = db.get(PeriodoLectivo, periodo_id)
    if not p:
        raise HTTPException(404, "Período não encontrado")
    return Response(docs.boletim(db, a, p), media_type="application/pdf")


@router.get("/educandos/{aid}/extracto.pdf", responses=R.PDF)
def extracto(aid: int, ee: Encarregado = Depends(encarregado_actual), db: Session = Depends(get_db)):
    return Response(docs.extracto(db, _educando(db, ee, aid)), media_type="application/pdf")


@router.get("/cobrancas", response_model=R.CobrancasPortal, summary="Cobranças de todos os educandos")
def cobrancas(estado: str | None = None, ee: Encarregado = Depends(encarregado_actual), db: Session = Depends(get_db)):
    linhas = _minhas_cobrancas(db, ee, estado)
    return {"linhas": linhas, "em_divida": sum(c["valor_em_aberto"] for c in linhas if c["estado"] != "paga")}


def _validar_cobrancas(db: Session, ee: Encarregado, ids: list[int]) -> Decimal:
    abertas = {c["cobranca_id"]: c for c in _minhas_cobrancas(db, ee, "divida")}
    if not ids or any(i not in abertas for i in ids):
        raise HTTPException(422, "Escolha cobranças em dívida dos seus educandos")
    return sum((Decimal(str(abertas[i]["valor_em_aberto"])) for i in ids), Decimal(0))


def _metodo(db: Session, est: int, tipo: str, nome: str) -> MetodoPagamento:
    m = db.scalar(select(MetodoPagamento).where(MetodoPagamento.estabelecimento_id == est, MetodoPagamento.tipo == tipo))
    if not m:
        m = MetodoPagamento(estabelecimento_id=est, nome=nome, tipo=tipo)
        db.add(m)
        db.flush()
    return m


@router.post("/pagamentos/referencia", response_model=R.ReferenciaGerada, status_code=201, summary="Gera uma referência Multicaixa para pagar cobranças")
def gerar_referencia(dados: ReferenciaIn, ee: Encarregado = Depends(encarregado_actual), db: Session = Depends(get_db)):
    valor = _validar_cobrancas(db, ee, dados.cobranca_ids)
    try:
        ref = gateway().criar_referencia(valor, f"CLACS {ee.nome_completo}")
    except ErroIntegracao as e:
        raise HTTPException(503, str(e))
    metodo = _metodo(db, ee.estabelecimento_id, "referencia", "Referência Multicaixa")
    pg = registar_pagamento(db, ee.estabelecimento_id, None, metodo_id=metodo.id, valor=valor, encarregado_id=ee.id,
                            cobranca_ids=dados.cobranca_ids, referencia=ref.referencia, estado="pendente")
    return {"pagamento_id": pg.id, "entidade": ref.entidade, "referencia": ref.referencia,
            "valor": float(ref.valor), "expira": ref.expira}


@router.post("/pagamentos/comprovativo", response_model=R.ComprovativoEnviado, status_code=201, summary="Envia o comprovativo de uma transferência ou depósito")
async def enviar_comprovativo(cobranca_ids: str = Form(..., description="ids separados por vírgula"),
                              referencia: str | None = Form(None), ficheiro: UploadFile = File(...),
                              ee: Encarregado = Depends(encarregado_actual), db: Session = Depends(get_db)):
    ids = [int(x) for x in cobranca_ids.replace(" ", "").split(",") if x.isdigit()]
    valor = _validar_cobrancas(db, ee, ids)
    chave = armazenamento.guardar(ee.estabelecimento_id, "comprovativos", await ficheiro.read(),
                                  ficheiro.content_type, ficheiro.filename)
    metodo = _metodo(db, ee.estabelecimento_id, "transferencia", "Transferência bancária")
    pg = registar_pagamento(db, ee.estabelecimento_id, None, metodo_id=metodo.id, valor=valor, encarregado_id=ee.id,
                            cobranca_ids=ids, referencia=referencia, comprovativo_url=armazenamento.url(chave),
                            estado="pendente")
    return {"pagamento_id": pg.id, "estado": pg.estado, "valor": float(pg.valor),
            "mensagem": "Comprovativo recebido. A secretaria confirma o pagamento depois de verificar."}


@router.get("/pagamentos", response_model=list[R.PagamentoPortal], summary="Pagamentos feitos e o seu estado")
def pagamentos(ee: Encarregado = Depends(encarregado_actual), db: Session = Depends(get_db)):
    rows = db.execute(select(Pagamento, MetodoPagamento.nome).join(MetodoPagamento, MetodoPagamento.id == Pagamento.metodo_id)
                      .where(Pagamento.encarregado_id == ee.id).order_by(Pagamento.id.desc()))
    return [{"id": p.id, "data": p.data_pagamento, "valor": float(p.valor), "metodo": m, "estado": p.estado,
             "referencia": p.referencia} for p, m in rows]


@router.get("/pagamentos/{pid}/recibo.pdf", responses=R.PDF)
def recibo(pid: int, ee: Encarregado = Depends(encarregado_actual), db: Session = Depends(get_db)):
    pg = db.get(Pagamento, pid)
    if not pg or pg.encarregado_id != ee.id:
        raise HTTPException(404, "Pagamento não encontrado")
    return Response(docs.recibo(db, pg), media_type="application/pdf")


@router.get("/mensagens", response_model=list[R.MensagemPortal], summary="Mensagens e comunicados recebidos")
def mensagens(ee: Encarregado = Depends(encarregado_actual), db: Session = Depends(get_db)):
    ms = db.scalars(select(Mensagem).where(Mensagem.encarregado_id == ee.id, Mensagem.estado != "falhada")
                    .order_by(Mensagem.id.desc()).limit(100))
    return [{"id": m.id, "canal": m.canal, "assunto": m.assunto, "corpo": m.corpo, "estado": m.estado,
             "data": m.enviada_em or m.agendada_para} for m in ms]


@router.post("/mensagens/{mid}/lida", status_code=204)
def marcar_lida(mid: int, ee: Encarregado = Depends(encarregado_actual), db: Session = Depends(get_db)):
    m = db.get(Mensagem, mid)
    if not m or m.encarregado_id != ee.id:
        raise HTTPException(404, "Mensagem não encontrada")
    m.estado = "lida"
    db.commit()


@router.put("/preferencias", response_model=R.PreferenciaPortal, summary="Activa ou desactiva um canal de contacto")
def preferencias(dados: PreferenciaPortalIn, ee: Encarregado = Depends(encarregado_actual), db: Session = Depends(get_db)):
    p = db.get(PreferenciaContacto, (ee.id, dados.canal)) or PreferenciaContacto(encarregado_id=ee.id, canal=dados.canal)
    p.activo = dados.activo
    db.add(p)
    db.commit()
    return {"canal": p.canal, "activo": p.activo}


class DispositivoIn(Entrada):
    token: str = Field(min_length=10, max_length=4096)
    plataforma: Literal["android", "ios", "web"] = "android"


@router.post("/dispositivos", response_model=R.DispositivoRegistado, status_code=201, summary="Regista o telemóvel/browser para notificações push")
def registar_dispositivo(dados: DispositivoIn, ctx: Contexto = Depends(contexto_actual),
                         ee: Encarregado = Depends(encarregado_actual), db: Session = Depends(get_db)):
    d = db.scalar(select(DispositivoPush).where(DispositivoPush.token == dados.token))
    if d:
        d.utilizador_id, d.plataforma = ctx.id, dados.plataforma  # o mesmo aparelho passou a outra conta
    else:
        db.add(DispositivoPush(utilizador_id=ctx.id, token=dados.token, plataforma=dados.plataforma))
    db.commit()
    return {"registado": True}


@router.delete("/dispositivos", status_code=204, summary="Deixa de enviar notificações para este aparelho")
def remover_dispositivo(token: str, ctx: Contexto = Depends(contexto_actual),
                        ee: Encarregado = Depends(encarregado_actual), db: Session = Depends(get_db)):
    db.execute(delete(DispositivoPush).where(DispositivoPush.token == token, DispositivoPush.utilizador_id == ctx.id))
    db.commit()


# ---------------------------------------------------------------- do lado da escola
@extra.post("/encarregados/{eid}/conta", response_model=R.ContaPortal, status_code=201, summary="Cria a conta do portal para um encarregado")
def criar_conta(eid: int, dados: ContaIn, ctx: Contexto = Depends(exige("utilizadores", "criar")),
                db: Session = Depends(get_db)):
    ee = db.get(Encarregado, eid)
    if not ee or ee.estabelecimento_id != ctx.estabelecimento_id:
        raise HTTPException(404, "Encarregado não encontrado")
    email = dados.email.strip().lower()
    if db.scalar(select(Utilizador.id).where(func.lower(Utilizador.email) == email)):
        raise HTTPException(409, "Já existe um utilizador com este e-mail")
    if db.scalar(select(Utilizador.id).where(Utilizador.encarregado_id == eid)):
        raise HTTPException(409, "Este encarregado já tem conta")
    u = Utilizador(nome=ee.nome_completo, email=email, telefone=ee.telefone, senha_hash=hash_senha(dados.senha),
                   encarregado_id=eid)
    db.add(u)
    db.flush()
    db.add(UtilizadorPerfil(utilizador_id=u.id, estabelecimento_id=ctx.estabelecimento_id,
                            perfil_id=db.scalar(select(Perfil.id).where(Perfil.codigo == "encarregado"))))
    auditar(db, ctx, "utilizador", u.id, "insert", {"email": email, "encarregado_id": eid})
    db.commit()
    return {"utilizador_id": u.id, "email": email}


@extra.post("/pagamentos/webhook", response_model=R.WebhookResultado, summary="Aviso do gateway: pagamento por referência concluído")
async def webhook(request: Request, db: Session = Depends(get_db)):
    corpo = await request.body()
    try:
        validar_webhook(corpo, request.headers.get("X-Assinatura"))
    except ErroIntegracao as e:
        raise HTTPException(401, str(e))
    try:
        dados = json.loads(corpo)
        ref, valor = str(dados["referencia"]), Decimal(str(dados["valor"]))
    except (ValueError, KeyError):
        raise HTTPException(422, "Corpo inválido: esperado {referencia, valor}")
    pg = db.scalar(select(Pagamento).join(MetodoPagamento, MetodoPagamento.id == Pagamento.metodo_id)
                   .where(Pagamento.referencia == ref, MetodoPagamento.tipo == "referencia"))
    if not pg:
        raise HTTPException(404, "Referência desconhecida")
    if pg.estado == "confirmado":
        return {"estado": "ja_confirmado"}
    if valor != pg.valor:
        log.warning("Webhook: valor %s diferente do esperado %s para a referência %s", valor, pg.valor, ref)
        raise HTTPException(409, "Valor diferente do esperado; o pagamento fica pendente para revisão")
    pg.data_pagamento = date.fromisoformat(str(dados.get("data", date.today()))[:10])
    mudar_estado_pagamento(db, pg, "confirmado")
    auditar(db, None, "pagamento", pg.id, "update", {"estado": "confirmado", "via": "webhook",
                                                     "transaccao": dados.get("id_transaccao")},
            estabelecimento_id=pg.estabelecimento_id)
    db.commit()
    return {"estado": "confirmado", "pagamento_id": pg.id}

