import os
import tempfile
from pathlib import Path

import pytest

# A base de testes tem de ser definida antes de importar a aplicação.
_tmp = Path(tempfile.mkdtemp()) / "teste.db"
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL", f"sqlite:///{_tmp.as_posix()}")
os.environ["ADMIN_PASSWORD"] = "senha-de-teste-123"
os.environ["JWT_SECRET"] = "segredo-de-testes-com-mais-de-32-bytes"
os.environ["UPLOAD_DIR"] = str(_tmp.parent / "uploads")
os.environ["PAGAMENTOS_WEBHOOK_SEGREDO"] = "segredo-webhook-testes"
os.environ["AGENDADOR_ACTIVO"] = "false"

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from scripts.seed import base, demo  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session", autouse=True)
def base_de_dados():
    cfg = Config(str(RAIZ / "alembic.ini"))
    cfg.set_main_option("script_location", str(RAIZ / "alembic"))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    with SessionLocal() as db:
        demo(db, base(db))
    yield


@pytest.fixture(scope="session")
def client():
    return TestClient(app)


def login(client, email, senha):
    r = client.post("/api/auth/login", data={"username": email, "password": senha})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["access_token"]}


@pytest.fixture(scope="session")
def admin(client):
    s = get_settings()
    return login(client, s.admin_email, s.admin_password)
