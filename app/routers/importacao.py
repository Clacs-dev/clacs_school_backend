"""Importação de dados do software actual (CSV exportado do Excel, separador ; ou ,)."""
import csv
import io
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.common import ano_activo, as_dict, obter
from app.database import get_db
from app.models import (
    Aluno, AlunoEncarregado, AnoLectivo, Cobranca, Encarregado, ImportacaoErro, ImportacaoLote, Matricula,
    MetodoPagamento, Pagamento, PagamentoAlocacao, PreferenciaContacto, Servico, Turma,
)
from app.security import Contexto, exige
from app.services.financeiro import actualizar_estado, encarregado_financeiro, valor_em_aberto

router = APIRouter(prefix="/api/importacao", tags=["Importação de dados"])

COLUNAS = {
    "encarregado": "nome_completo;nif;telefone;whatsapp;email;profissao;morada",
    "aluno": "nome_completo;numero_processo;data_nascimento;sexo;bi_numero;turma;encarregado_nif;encarregado_nome;parentesco",
    "cobranca": "numero_processo;servico_codigo;competencia;valor;vencimento;estado",
    "pagamento": "numero_processo;servico_codigo;competencia;valor;data;metodo;referencia",
}
PARENTESCO = {"mãe": "mae", "mae": "mae", "pai": "pai", "tutor": "tutor"}


def _data(v: str | None) -> date | None:
    if not v:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(v.strip(), fmt).date()
        except ValueError:
            pass
    raise ValueError(f"data inválida: {v}")


def _ler_csv(conteudo: bytes) -> list[dict]:
    texto = None
    for enc in ("utf-8-sig", "cp1252"):
        try:
            texto = conteudo.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if texto is None:
        raise HTTPException(422, "Não foi possível ler o ficheiro (codificação desconhecida)")
    primeira = texto.splitlines()[0] if texto else ""
    sep = ";" if primeira.count(";") >= primeira.count(",") else ","
    leitor = csv.DictReader(io.StringIO(texto), delimiter=sep)
    return [{(k or "").strip().lower(): (v or "").strip() for k, v in linha.items()} for linha in leitor]


@router.get("/modelos", summary="Colunas esperadas em cada ficheiro")
def modelos(ctx: Contexto = Depends(exige("importacao"))):
    return COLUNAS


@router.post("/{entidade}", status_code=201)
async def importar(entidade: Literal["aluno", "encarregado", "cobranca", "pagamento"], ficheiro: UploadFile = File(...),
                   origem: str = Form("software anterior"), ctx: Contexto = Depends(exige("importacao", "criar")),
                   db: Session = Depends(get_db)):
    est = ctx.estabelecimento_id
    linhas = _ler_csv(await ficheiro.read())
    lote = ImportacaoLote(estabelecimento_id=est, origem=origem, entidade=entidade, ficheiro=ficheiro.filename,
                          total_linhas=len(linhas), criado_por=ctx.id)
    db.add(lote)
    db.commit()
    ok = 0
    ano = ano_activo(db, est, obrigatorio=False)
    turmas = {}
    if ano:
        turmas = {t.designacao.strip().lower(): t for t in db.scalars(
            select(Turma).where(Turma.estabelecimento_id == est, Turma.ano_id == ano.id))}
    for n, l in enumerate(linhas, start=2):  # linha 1 = cabeçalho
        try:
            with db.begin_nested():
                if entidade in ("aluno", "encarregado") and not l.get("nome_completo"):
                    raise ValueError("nome_completo em falta")
                if entidade == "encarregado":
                    _importar_encarregado(db, est, l)
                elif entidade == "aluno":
                    _importar_aluno(db, est, l, ano, turmas, ctx.id)
                elif entidade == "cobranca":
                    _importar_cobranca(db, est, l)
                else:
                    _importar_pagamento(db, est, l, ctx.id)
            ok += 1
        except Exception as e:  # cada linha falha sozinha e fica registada
            db.add(ImportacaoErro(lote_id=lote.id, linha=n, mensagem=str(e)[:500], dados=l))
    lote.linhas_ok, lote.estado = ok, "concluido" if ok or not linhas else "erro"
    db.commit()
    return {**as_dict(lote), "erros": len(linhas) - ok}


def _importar_encarregado(db: Session, est: int, l: dict) -> Encarregado:
    if l.get("nif") and db.scalar(select(Encarregado.id).where(Encarregado.estabelecimento_id == est,
                                                               Encarregado.nif == l["nif"])):
        raise ValueError(f"encarregado com NIF {l['nif']} já existe")
    e = Encarregado(estabelecimento_id=est, nome_completo=l["nome_completo"], nif=l.get("nif") or None,
                    telefone=l.get("telefone") or None, whatsapp=l.get("whatsapp") or l.get("telefone") or None,
                    email=l.get("email") or None, profissao=l.get("profissao") or None, morada=l.get("morada") or None)
    db.add(e)
    db.flush()
    for canal in ("whatsapp", "sms", "email", "app"):
        db.add(PreferenciaContacto(encarregado_id=e.id, canal=canal))
    return e


def _importar_aluno(db: Session, est: int, l: dict, ano, turmas: dict, uid: int) -> None:
    num = l.get("numero_processo")
    if not num:
        total = db.scalar(select(func.count()).select_from(Aluno).where(Aluno.estabelecimento_id == est))
        num = f"IMP{total + 1:05d}"
    if db.scalar(select(Aluno.id).where(Aluno.estabelecimento_id == est, Aluno.numero_processo == num)):
        raise ValueError(f"número de processo {num} já existe")
    sexo = (l.get("sexo") or "").upper()[:1] or None
    a = Aluno(estabelecimento_id=est, numero_processo=num, nome_completo=l["nome_completo"],
              data_nascimento=_data(l.get("data_nascimento")), sexo=sexo if sexo in ("F", "M") else None,
              bi_numero=l.get("bi_numero") or None)
    db.add(a)
    db.flush()
    ee = None
    if l.get("encarregado_nif"):
        ee = db.scalar(select(Encarregado).where(Encarregado.estabelecimento_id == est,
                                                 Encarregado.nif == l["encarregado_nif"]))
    if ee is None and l.get("encarregado_nome"):
        ee = db.scalar(select(Encarregado).where(Encarregado.estabelecimento_id == est,
                                                 func.lower(Encarregado.nome_completo) == l["encarregado_nome"].lower()))
    if ee:
        db.add(AlunoEncarregado(aluno_id=a.id, encarregado_id=ee.id,
                                parentesco=PARENTESCO.get((l.get("parentesco") or "").lower(), "outro"),
                                principal=True, responsavel_financeiro=True))
    if l.get("turma"):
        t = turmas.get(l["turma"].strip().lower())
        if not t:
            raise ValueError(f"turma '{l['turma']}' não existe no ano activo")
        db.add(Matricula(estabelecimento_id=est, aluno_id=a.id, ano_id=ano.id, turma_id=t.id, tipo="nova",
                         registado_por=uid))


@router.get("/lotes")
def lotes(ctx: Contexto = Depends(exige("importacao")), db: Session = Depends(get_db)):
    return [as_dict(l) for l in db.scalars(select(ImportacaoLote).where(ImportacaoLote.estabelecimento_id == ctx.estabelecimento_id)
                                           .order_by(ImportacaoLote.id.desc()))]


@router.get("/lotes/{lid}/erros")
def erros(lid: int, ctx: Contexto = Depends(exige("importacao")), db: Session = Depends(get_db)):
    obter(db, ImportacaoLote, lid, ctx.estabelecimento_id, "Lote")
    return [as_dict(e) for e in db.scalars(select(ImportacaoErro).where(ImportacaoErro.lote_id == lid)
                                           .order_by(ImportacaoErro.linha))]


# ---------------------------------------------------------------- histórico financeiro
def _competencia(v: str | None) -> date:
    """'2026-09', '09/2026', '2026-09-01' ou '01/09/2026' → primeiro dia do mês."""
    v = (v or "").strip()
    for fmt in ("%Y-%m", "%m/%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(v, fmt).date().replace(day=1)
        except ValueError:
            pass
    raise ValueError(f"competência inválida: {v!r} (use AAAA-MM ou MM/AAAA)")


def _valor(v: str | None) -> Decimal:
    texto = (v or "").replace(" ", "").replace("Kz", "")
    if "," in texto:  # 45.000,50 → 45000.50
        texto = texto.replace(".", "").replace(",", ".")
    try:
        d = Decimal(texto)
    except InvalidOperation:
        raise ValueError(f"valor inválido: {v!r}")
    if d <= 0:
        raise ValueError("o valor tem de ser positivo")
    return d


def _matricula_servico(db: Session, est: int, l: dict) -> tuple[Matricula, Servico, date]:
    aluno = db.scalar(select(Aluno).where(Aluno.estabelecimento_id == est, Aluno.numero_processo == l.get("numero_processo")))
    if not aluno:
        raise ValueError(f"aluno com processo {l.get('numero_processo')!r} não existe")
    servico = db.scalar(select(Servico).where(Servico.estabelecimento_id == est,
                                              func.upper(Servico.codigo) == (l.get("servico_codigo") or "").upper()))
    if not servico:
        raise ValueError(f"serviço {l.get('servico_codigo')!r} não existe")
    comp = _competencia(l.get("competencia"))
    m = db.scalar(select(Matricula).join(AnoLectivo, AnoLectivo.id == Matricula.ano_id)
                  .where(Matricula.aluno_id == aluno.id, AnoLectivo.data_inicio <= comp, AnoLectivo.data_fim >= comp))
    if not m:
        raise ValueError(f"o aluno não tem matrícula no ano lectivo de {comp:%m/%Y}")
    return m, servico, comp


def _metodo_importacao(db: Session, est: int, nome: str | None) -> MetodoPagamento:
    nome = (nome or "Importado do sistema anterior").strip()
    m = db.scalar(select(MetodoPagamento).where(MetodoPagamento.estabelecimento_id == est,
                                                func.lower(MetodoPagamento.nome) == nome.lower()))
    if not m:
        tipo = next((t for t in ("transferencia", "multicaixa", "tpa", "deposito", "referencia") if t in nome.lower()), "numerario")
        m = MetodoPagamento(estabelecimento_id=est, nome=nome, tipo=tipo)
        db.add(m)
        db.flush()
    return m


def _pagar(db: Session, est: int, c: Cobranca, valor: Decimal, data_pag: date, metodo: MetodoPagamento,
           referencia: str | None, uid: int | None) -> None:
    aberto = valor_em_aberto(db, c)
    if valor > aberto:
        raise ValueError(f"o pagamento ({valor}) é maior do que o valor em aberto ({aberto})")
    pg = Pagamento(estabelecimento_id=est, encarregado_id=c.encarregado_id, metodo_id=metodo.id, valor=valor,
                   data_pagamento=data_pag, referencia=referencia, estado="confirmado", registado_por=uid)
    db.add(pg)
    db.flush()
    db.add(PagamentoAlocacao(pagamento_id=pg.id, cobranca_id=c.id, valor=valor))
    db.flush()
    actualizar_estado(db, c)


def _importar_cobranca(db: Session, est: int, l: dict) -> None:
    m, servico, comp = _matricula_servico(db, est, l)
    if db.scalar(select(Cobranca.id).where(Cobranca.matricula_id == m.id, Cobranca.servico_id == servico.id,
                                           Cobranca.competencia == comp)):
        raise ValueError(f"a cobrança de {servico.codigo} {comp:%m/%Y} já existe")
    valor = _valor(l.get("valor"))
    c = Cobranca(estabelecimento_id=est, matricula_id=m.id, aluno_id=m.aluno_id,
                 encarregado_id=encarregado_financeiro(db, m.aluno_id), servico_id=servico.id, competencia=comp,
                 descricao=f"{servico.nome} {comp:%m/%Y} (importada)", valor_base=valor,
                 vencimento=_data(l.get("vencimento")) or comp.replace(day=10))
    db.add(c)
    db.flush()
    if (l.get("estado") or "").strip().lower() in ("paga", "pago", "liquidada"):
        _pagar(db, est, c, valor, c.vencimento, _metodo_importacao(db, est, None), "importação", None)


def _importar_pagamento(db: Session, est: int, l: dict, uid: int) -> None:
    m, servico, comp = _matricula_servico(db, est, l)
    c = db.scalar(select(Cobranca).where(Cobranca.matricula_id == m.id, Cobranca.servico_id == servico.id,
                                         Cobranca.competencia == comp, Cobranca.estado != "anulada"))
    if not c:
        raise ValueError(f"não existe cobrança de {servico.codigo} {comp:%m/%Y} para este aluno; importe-a primeiro")
    _pagar(db, est, c, _valor(l.get("valor")), _data(l.get("data")) or date.today(),
           _metodo_importacao(db, est, l.get("metodo")), l.get("referencia") or None, uid)

