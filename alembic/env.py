import logging
from logging.config import fileConfig

from alembic import context

from app.config import get_settings
from app.database import make_engine
from app.models import Base

config = context.config
if config.config_file_name is not None:
    # Sem isto, correr as migrações dentro da API desligava os registos (logs) da própria API.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata
URL = config.attributes.get("url") or get_settings().sqlalchemy_url


def run_migrations_offline() -> None:
    context.configure(url=URL, target_metadata=target_metadata, literal_binds=True,
                      dialect_opts={"paramstyle": "named"}, render_as_batch=URL.startswith("sqlite"))
    with context.begin_transaction():
        context.run_migrations()


def _libertar_bloqueios(connection) -> None:
    """Num deploy, a versão anterior da API continua ligada à base. Uma ligação dela parada a meio de uma transacção
    prende as tabelas e um ALTER TABLE da migração ficava à espera para sempre: a API nunca abria a porta e o Render
    desistia ("Port scan timeout"). Aqui fecham-se essas ligações esquecidas e limita-se a espera por bloqueios,
    para a migração avançar ou falhar depressa com o motivo nos registos."""
    log = logging.getLogger("clacs.arranque")
    fechadas = connection.exec_driver_sql(
        "SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity "
        "WHERE datname = current_database() AND pid <> pg_backend_pid() "
        "AND state IN ('idle in transaction', 'idle in transaction (aborted)') "
        "AND now() - state_change > interval '30 seconds'").scalar()
    if fechadas:
        log.warning("Migrações: fechadas %s ligações paradas a meio de uma transacção (prendiam tabelas)", fechadas)
    connection.exec_driver_sql("SET lock_timeout = '20s'")
    connection.commit()


def run_migrations_online() -> None:
    engine = make_engine(URL)
    with engine.connect() as connection:
        if connection.dialect.name == "sqlite":
            # No SQLite, alterar uma tabela é recriá-la (batch). Com as chaves estrangeiras ligadas, apagar a tabela
            # antiga falha quando há linhas noutras tabelas a apontar para ela (ex.: turmas → sala).
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            connection.commit()
        elif connection.dialect.name == "postgresql":
            _libertar_bloqueios(connection)
        context.configure(connection=connection, target_metadata=target_metadata,
                          render_as_batch=connection.dialect.name == "sqlite", compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
