import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse

from app import agendador, arranque
from app.config import get_settings
from app.routers import (
    academico, auth, cadastro, comunicacao, documentos, financeiro, importacao, painel, pedagogico, portal,
    primavera, refeitorio,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("clacs.http")


@asynccontextmanager
async def ciclo_de_vida(_: FastAPI):
    if get_settings().auto_migrar:
        arranque.preparar_base()
    if get_settings().agendador_activo:
        agendador.iniciar()
    yield
    agendador.parar()


app = FastAPI(
    title="CLACS Escola API",
    version="1.1.0",
    description="Gestão escolar do Colégio Cota Lionel: cadastro, propinas, refeitório, pedagógico, "
                "comunicação, alertas, portal do encarregado e integração Primavera.",
    docs_url="/api/docs", redoc_url="/api/redoc", openapi_url="/api/openapi.json",
    lifespan=ciclo_de_vida,
)

origens = [o.strip() for o in get_settings().cors_origins.split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origens, allow_credentials="*" not in origens,
                   allow_methods=["*"], allow_headers=["*"], expose_headers=["Content-Disposition", "X-2FA"])


@app.middleware("http")
async def registo_e_cabecalhos(request: Request, call_next):
    """Identificador por pedido, tempo de resposta no registo, cabeçalhos de segurança e erros sem fugas."""
    pid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    inicio = time.perf_counter()
    try:
        resposta = await call_next(request)
    except Exception:
        log.exception("Erro não tratado [%s] %s %s", pid, request.method, request.url.path)
        resposta = JSONResponse({"detail": f"Erro interno. Referência: {pid}"}, status_code=500)
    ms = (time.perf_counter() - inicio) * 1000
    if request.url.path.startswith("/api") and request.url.path != "/api/health":
        log.info("[%s] %s %s -> %s (%.0f ms)", pid, request.method, request.url.path, resposta.status_code, ms)
    resposta.headers["X-Request-ID"] = pid
    resposta.headers["X-Content-Type-Options"] = "nosniff"
    resposta.headers["X-Frame-Options"] = "DENY"
    resposta.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return resposta


for r in (auth, academico, cadastro, financeiro, refeitorio, comunicacao, pedagogico, painel, primavera,
          importacao, documentos, portal):
    app.include_router(r.router)
app.include_router(portal.extra)


@app.get("/", include_in_schema=False)
def raiz():
    # O frontend (Vite + React) está em clacs-frontend/ e é publicado à parte.
    return RedirectResponse("/api/docs")
