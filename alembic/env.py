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


def run_migrations_online() -> None:
    engine = make_engine(URL)
    with engine.connect() as connection:
        if connection.dialect.name == "sqlite":
            # No SQLite, alterar uma tabela é recriá-la (batch). Com as chaves estrangeiras ligadas, apagar a tabela
            # antiga falha quando há linhas noutras tabelas a apontar para ela (ex.: turmas → sala).
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            connection.commit()
        context.configure(connection=connection, target_metadata=target_metadata,
                          render_as_batch=connection.dialect.name == "sqlite", compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
