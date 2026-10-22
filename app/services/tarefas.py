"""Tarefas periódicas, partilhadas pelo agendador interno e pelo script scripts/tarefas_diarias.py."""
import logging
from datetime import date

from sqlalchemy import select

from app.database import SessionLocal
from app.models import Estabelecimento
from app.services.alertas import persistir_alertas
from app.services.comunicacao import despachar, executar_regras
from app.services.financeiro import aplicar_multas, configuracao_cobranca, gerar_cobrancas
from app.services.relatorios import executar_devidos

log = logging.getLogger("clacs.tarefas")


def diarias(hoje: date | None = None) -> dict:
    """Cobranças do mês (dia 1), multas, regras, alertas e envio da fila, para cada estabelecimento activo."""
    hoje = hoje or date.today()
    resumo = {}
    with SessionLocal() as db:
        for est in db.scalars(select(Estabelecimento).where(Estabelecimento.activo)).all():
            r = {}
            try:
                if hoje.day == 1:
                    pendentes = configuracao_cobranca(db, est.id)["pendentes"]
                    if pendentes:  # como no botão "Gerar": sem propina, preços e multa configurados, não gera
                        log.warning("Cobranças de %s não geradas; falta: %s", est.nome, "; ".join(pendentes))
                        r["cobrancas_por_configurar"] = pendentes
                    else:
                        r["cobrancas"] = gerar_cobrancas(db, est.id, hoje)["criadas"]
                r["multas"] = aplicar_multas(db, est.id, hoje)
                r["regras"] = sum(executar_regras(db, est.id, hoje).values())
                r["alertas"] = persistir_alertas(db, est.id, hoje)
                r["envio"] = despachar(db, est.id)
            except Exception:  # um estabelecimento com erro não pára os outros
                db.rollback()
                log.exception("Tarefas diárias de %s falharam", est.nome)
                r["erro"] = True
            log.info("Tarefas diárias de %s: %s", est.nome, r)
            resumo[est.nome] = r
    return resumo


def fila_de_mensagens() -> None:
    with SessionLocal() as db:
        for est_id in db.scalars(select(Estabelecimento.id).where(Estabelecimento.activo)).all():
            despachar(db, est_id)


def relatorios() -> None:
    with SessionLocal() as db:
        n = executar_devidos(db)
        if n:
            log.info("%s relatório(s) agendado(s) enviados", n)
