"""Relatórios agendados: gerar o ficheiro, guardá-lo e enviá-lo por e-mail aos destinatários."""
import logging
from datetime import datetime, timedelta, timezone

from apscheduler.triggers.cron import CronTrigger
from reportlab.platypus import Spacer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import armazenamento, pdf
from app.config import get_settings
from app.exportar import TIPOS, ficheiro
from app.integracoes import ErroIntegracao
from app.integracoes.email import enviar_email
from app.models import Estabelecimento, RelatorioAgendado, RelatorioExecucao
from app.services import financeiro as fin

log = logging.getLogger("clacs.relatorios")

COLUNAS = {
    "cobrancas": [("aluno", "Aluno"), ("turma", "Turma"), ("encarregado", "Encarregado"), ("servico", "Serviço"),
                  ("competencia", "Competência"), ("valor_total", "Valor (Kz)"), ("valor_pago", "Pago (Kz)"),
                  ("valor_em_aberto", "Em aberto (Kz)"), ("estado", "Estado"), ("dias_atraso", "Dias de atraso")],
    "devedores": [("aluno", "Aluno"), ("turma", "Turma"), ("encarregado", "Encarregado"), ("telefone", "Telefone"),
                  ("cobrancas_em_atraso", "Cobranças em atraso"), ("total_em_divida", "Total em dívida (Kz)"),
                  ("maior_atraso", "Maior atraso (dias)")],
    "cobranca_turma": [("turma", "Turma"), ("alunos", "Alunos"), ("facturado", "Facturado (Kz)"),
                       ("recebido", "Recebido (Kz)"), ("em_atraso", "Em atraso (Kz)"), ("taxa_cobranca", "Taxa (%)")],
    "refeitorio": [("aluno", "Aluno"), ("turma", "Turma"), ("servico", "Serviço"), ("competencia", "Mês"),
                   ("valor_total", "Valor (Kz)"), ("estado", "Estado")],
}
TITULOS = {"cobrancas": "Mapa de cobranças", "devedores": "Devedores", "cobranca_turma": "Cobrança por turma",
           "refeitorio": "Refeitório e alimentação"}


def linhas(db: Session, est: int, tipo: str, filtros: dict | None) -> list[dict]:
    f = filtros or {}
    if tipo == "devedores":
        return fin.devedores(db, est)
    if tipo == "cobranca_turma":
        return fin.resumo_por_turma(db, est)
    if tipo == "refeitorio":
        return [x for x in fin.cobrancas_detalhe(db, est, estado=f.get("estado"))
                if x["categoria"] in ("alimentacao", "refeitorio")]
    return fin.cobrancas_detalhe(db, est, turma_id=f.get("turma_id"), estado=f.get("estado"), categoria=f.get("categoria"))


def gerar(db: Session, est: int, tipo: str, filtros: dict | None, formato: str) -> tuple[str, bytes, str]:
    dados = linhas(db, est, tipo, filtros)
    cols = COLUNAS[tipo]
    if formato != "pdf":
        return ficheiro(cols, dados, tipo, formato)
    corpo = [[c[1] for c in cols]] + [[pdf.esc(l.get(k)) for k, _ in cols] for l in dados]
    conteudo = pdf.gerar(db.get(Estabelecimento, est), TITULOS[tipo],
                         [pdf.p(f"{len(dados)} registo(s)"), Spacer(1, 8), pdf.tabela(corpo)], horizontal=len(cols) > 6)
    return f"{tipo}-{datetime.now():%Y%m%d}.pdf", conteudo, TIPOS["pdf"]


def executar(db: Session, rel: RelatorioAgendado) -> RelatorioExecucao:
    try:
        nome, dados, mime = gerar(db, rel.estabelecimento_id, rel.tipo, rel.filtros, rel.formato)
        chave = armazenamento.guardar(rel.estabelecimento_id, "relatorios", dados, mime.split(";")[0], nome)
        enviar_email(list(rel.destinatarios), f"CLACS Escola: {rel.nome}",
                     f"Segue em anexo o relatório «{rel.nome}», gerado em {datetime.now():%d/%m/%Y %H:%M}.",
                     anexos=[(nome, dados, mime.split(";")[0])])
        ex = RelatorioExecucao(relatorio_id=rel.id, estado="ok", ficheiro_url=armazenamento.url(chave))
    except (ErroIntegracao, OSError, ValueError) as e:
        log.exception("Relatório %s falhou", rel.id)
        ex = RelatorioExecucao(relatorio_id=rel.id, estado="erro", ficheiro_url=None)
        db.add(ex)
        db.commit()
        raise ErroIntegracao(f"O relatório falhou: {e}") from e
    db.add(ex)
    db.commit()
    return ex


def proxima_execucao(rel: RelatorioAgendado, depois_de: datetime) -> datetime | None:
    trig = CronTrigger.from_crontab(rel.agenda_cron, timezone=get_settings().fuso_horario)
    return trig.get_next_fire_time(None, depois_de)


def executar_devidos(db: Session, agora: datetime | None = None, janela_min: int = 15) -> int:
    """Executa os relatórios cujo horário (cron) passou desde a última execução."""
    agora = agora or datetime.now(timezone.utc)
    n = 0
    for rel in db.scalars(select(RelatorioAgendado).where(RelatorioAgendado.activo)).all():
        ultima = db.scalar(select(RelatorioExecucao.executado_em).where(RelatorioExecucao.relatorio_id == rel.id)
                           .order_by(RelatorioExecucao.executado_em.desc()).limit(1))
        base = ultima if ultima else agora - timedelta(minutes=janela_min)
        if base.tzinfo is None:
            base = base.replace(tzinfo=timezone.utc)
        proxima = proxima_execucao(rel, base)
        if proxima and proxima <= agora:
            try:
                executar(db, rel)
                n += 1
            except ErroIntegracao:
                pass
    return n
