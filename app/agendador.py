"""Agendador interno: substitui o cron job pago do Render.

Liga-se com AGENDADOR_ACTIVO=true. Corre dentro do processo da API, por isso:
- use UMA só instância (ou um só worker) com o agendador ligado, senão as tarefas correm em duplicado;
- no plano gratuito do Render a API adormece sem tráfego e as tarefas não correm enquanto dorme.
"""
import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.config import get_settings
from app.services import tarefas

log = logging.getLogger("clacs.agendador")
_agendador: BackgroundScheduler | None = None


def iniciar() -> BackgroundScheduler:
    global _agendador
    if _agendador and _agendador.running:
        return _agendador
    s = get_settings()
    hora, minuto = (int(x) for x in s.agendador_hora.split(":"))
    ag = BackgroundScheduler(timezone=s.fuso_horario, job_defaults={"coalesce": True, "max_instances": 1,
                                                                    "misfire_grace_time": 3600})
    ag.add_job(tarefas.diarias, CronTrigger(hour=hora, minute=minuto), id="tarefas_diarias",
               name="Cobranças, multas, regras, alertas e envio")
    ag.add_job(tarefas.fila_de_mensagens, IntervalTrigger(minutes=10), id="fila", name="Envio da fila de mensagens")
    ag.add_job(tarefas.relatorios, IntervalTrigger(minutes=15), id="relatorios", name="Relatórios agendados")
    ag.start()
    _agendador = ag
    log.info("Agendador ligado: tarefas diárias às %s (%s)", s.agendador_hora, s.fuso_horario)
    return ag


def parar() -> None:
    global _agendador
    if _agendador and _agendador.running:
        _agendador.shutdown(wait=False)
    _agendador = None


def estado() -> dict:
    if not (_agendador and _agendador.running):
        return {"activo": False, "tarefas": []}
    return {"activo": True, "tarefas": [{"id": j.id, "nome": j.name, "proxima": j.next_run_time}
                                        for j in _agendador.get_jobs()]}
