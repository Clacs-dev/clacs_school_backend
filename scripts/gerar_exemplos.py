"""Gera app/exemplos_respostas.json: uma resposta real de cada rota GET, para o "Example Value" do /api/docs.

    python -m scripts.gerar_exemplos

Usa uma base SQLite temporária com os dados de demonstração (não toca na base configurada no .env).
Volte a correr depois de mudar as respostas; o teste test_exemplos_de_resposta avisa se ficarem desactualizados.
"""
import json
import os
import re
import tempfile
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DESTINO = RAIZ / "app" / "exemplos_respostas.json"
SENHA = "senha-de-exemplo-123"

_tmp = Path(tempfile.mkdtemp())
os.environ.update(DATABASE_URL=f"sqlite:///{(_tmp / 'exemplos.db').as_posix()}", ADMIN_PASSWORD=SENHA,
                  JWT_SECRET="segredo-so-para-gerar-exemplos-123456", UPLOAD_DIR=str(_tmp / "uploads"),
                  AGENDADOR_ACTIVO="false", AUTO_MIGRAR="false")

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.routing import APIRoute  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app import schemas as S  # noqa: E402
from app.main import app  # noqa: E402
from scripts.seed import base, demo  # noqa: E402

MAX_ITENS = 2  # listas de registos encurtadas para o exemplo ficar legível
FILTROS = {"/api/avaliacoes": {"turma_id": 1}, "/api/pautas": {"turma_id": 1, "periodo_id": 1},
           "/api/registo-diario": {"turma_id": 1, "data": "2026-10-20"},
           "/api/refeitorio/registos": {"data": "2026-10-20"}}  # parâmetros obrigatórios ou datas dos exemplos

# Registos que os dados de demonstração não trazem, criados com os pedidos de exemplo para que as listas
# correspondentes também tenham uma resposta de exemplo.
POVOAR = [("POST", "/api/comunicados", S.ComunicadoIn), ("POST", "/api/alunos/1/mensagem", S.MensagemAlunoIn),
          ("POST", "/api/ocorrencias", S.OcorrenciaIn), ("POST", "/api/registo-diario", S.RegistoDiarioIn),
          ("PUT", "/api/refeitorio/ementas", S.EmentaIn), ("POST", "/api/refeitorio/registos", S.RefeicaoRegistoIn),
          ("POST", "/api/matriculas/1/turma", S.MudancaTurmaIn),
          ("POST", "/api/matriculas/1/descontos", S.MatriculaDescontoIn),
          ("POST", "/api/relatorios/agendados", S.RelatorioIn), ("POST", "/api/relatorios/agendados/1/executar", None),
          ("POST", "/api/alertas/gerar", None)]


def encurtar(valor):
    if isinstance(valor, list):
        registos = valor and all(isinstance(v, dict) for v in valor)
        return [encurtar(v) for v in (valor[:MAX_ITENS] if registos else valor)]
    if isinstance(valor, dict):
        return {k: encurtar(v) for k, v in valor.items()}
    return valor


def main():
    cfg = Config(str(RAIZ / "alembic.ini"))
    cfg.set_main_option("script_location", str(RAIZ / "alembic"))
    command.upgrade(cfg, "head")
    with SessionLocal() as db:
        demo(db, base(db), hoje=date(2026, 10, 15))

    cliente = TestClient(app)

    def entrar(email):
        r = cliente.post("/api/auth/login", data={"username": email, "password": SENHA})
        r.raise_for_status()
        return {"Authorization": "Bearer " + r.json()["access_token"]}

    admin = entrar(get_settings().admin_email)
    encarregado = entrar("encarregado@clacs.ao")

    for metodo, caminho, esquema in POVOAR:
        r = cliente.request(metodo, caminho, json=S.EXEMPLOS[esquema] if esquema else None, headers=admin)
        if r.status_code >= 400:
            print(f"  aviso: {metodo} {caminho} -> {r.status_code} {r.text[:120]}")

    exemplos, ignoradas = {}, []
    for rota in app.routes:
        if not isinstance(rota, APIRoute) or "GET" not in rota.methods or not rota.include_in_schema:
            continue
        if rota.response_model is None:  # ficheiros (PDF, Excel)
            continue
        caminho = re.sub(r"\{[^}]+\}", "1", rota.path)
        cabecalhos = encarregado if rota.path.startswith("/api/portal") else admin
        r = cliente.get(caminho, params=FILTROS.get(rota.path), headers=cabecalhos)
        if r.status_code == 200 and r.json() != []:  # lista vazia: fica o exemplo gerado do esquema
            exemplos[f"GET {rota.path}"] = encurtar(r.json())
        else:
            ignoradas.append(f"GET {rota.path} -> {r.status_code}")

    DESTINO.write_text(json.dumps(exemplos, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"{len(exemplos)} exemplos gravados em {DESTINO.relative_to(RAIZ)}")
    for linha in ignoradas:
        print("  sem exemplo:", linha)


if __name__ == "__main__":
    main()
