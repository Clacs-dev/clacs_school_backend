"""Tarefas diárias de cada estabelecimento: cobranças do mês, multas, regras, alertas e envio da fila.

    python -m scripts.tarefas_diarias

Alternativa ao agendador interno (AGENDADOR_ACTIVO=true), para correr por cron fora da API.
"""
import logging

from app.services.tarefas import diarias, relatorios

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

if __name__ == "__main__":
    diarias()
    relatorios()
