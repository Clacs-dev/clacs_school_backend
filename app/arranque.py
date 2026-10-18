"""Preparação da base de dados quando a API arranca.

Aplica as migrações Alembic em falta e cria os dados iniciais (perfis, permissões, administrador e, com
SEED_DEMO=true, os dados de demonstração). Assim a API fica pronta mesmo que o comando de arranque do servidor seja
só `uvicorn app.main:app`. Desliga-se com AUTO_MIGRAR=false (por exemplo, com várias instâncias da API).
"""
import logging
import os
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.config import get_settings
from app.database import SessionLocal

log = logging.getLogger("clacs.arranque")
RAIZ = Path(__file__).resolve().parent.parent


def preparar_base() -> None:
    s = get_settings()
    cfg = Config(str(RAIZ / "alembic.ini"))
    cfg.set_main_option("script_location", str(RAIZ / "alembic"))
    cfg.attributes["url"] = s.sqlalchemy_url
    log.info("A aplicar as migrações da base de dados…")
    command.upgrade(cfg, "head")

    from scripts.seed import base, demo  # importado aqui: o seed importa a aplicação inteira
    with SessionLocal() as db:
        est = base(db)
        if os.environ.get("SEED_DEMO", "").lower() == "true" and demo(db, est):
            log.info("Dados de demonstração carregados.")
    log.info("Base de dados pronta.")
