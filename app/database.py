from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings


def make_engine(url: str):
    kwargs = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    elif url.startswith("postgresql"):
        # Não ficar pendurado se a base não responder; e o PostgreSQL fecha transacções esquecidas (abertas e
        # paradas) ao fim de 2 minutos, para não prenderem tabelas (ex.: às migrações de um novo deploy).
        kwargs["connect_args"] = {"connect_timeout": 10}
    engine = create_engine(url, **kwargs)
    if url.startswith("postgresql"):
        @event.listens_for(engine, "connect")
        def _sem_transaccoes_esquecidas(dbapi_conn, _):
            with dbapi_conn.cursor() as cur:
                cur.execute("SET idle_in_transaction_session_timeout = 120000")
            dbapi_conn.commit()
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_conn, _):
            dbapi_conn.execute("PRAGMA foreign_keys=ON")
    return engine


engine = make_engine(get_settings().sqlalchemy_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
