"""Migrações sobre uma base COM dados (corre por último: a base já tem tudo o que os outros testes criaram).

No SQLite, alterar uma tabela é recriá-la; com chaves estrangeiras ligadas isso falhava quando outras tabelas
apontavam para ela (ex.: turmas → sala na migração 0004). Este teste desce até à 0002 e volta a subir.
"""
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from app.database import engine
from tests.conftest import RAIZ


def test_descer_e_subir_migracoes_com_dados():
    cfg = Config(str(RAIZ / "alembic.ini"))
    cfg.set_main_option("script_location", str(RAIZ / "alembic"))
    with engine.connect() as c:
        antes = {t: c.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar() for t in ("sala", "turma", "aluno", "funcionario")}
    assert antes["turma"] > 0
    engine.dispose()
    command.downgrade(cfg, "0002")
    command.upgrade(cfg, "head")
    engine.dispose()
    with engine.connect() as c:
        depois = {t: c.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar() for t in antes}
        assert depois == antes
        assert c.execute(text("SELECT COUNT(*) FROM turma WHERE sala_id IS NOT NULL")).scalar() > 0
        if c.dialect.name == "sqlite":
            assert c.execute(text("PRAGMA foreign_key_check")).fetchall() == []
