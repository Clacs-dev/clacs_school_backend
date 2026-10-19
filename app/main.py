import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

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


DESCRICAO = """
Gestão escolar do Colégio Cota Lionel: cadastro, propinas, refeitório, pedagógico, comunicação, alertas,
portal do encarregado e integração Primavera.

## Autenticação
1. `POST /api/auth/login` com **form-urlencoded** `username` (e-mail) e `password`
   (e `codigo` se a conta tiver verificação em dois passos; a resposta 401 traz o cabeçalho `X-2FA: required`).
2. A resposta traz `access_token`. Envie-o em todos os pedidos: `Authorization: Bearer <access_token>`.
3. O token dura 12 horas. Uma resposta **401** significa sessão expirada: volte ao login.
4. `GET /api/auth/me` diz quem está ligado, os perfis e as permissões (`modulo:accao`) para mostrar ou esconder menus.

## Convenções
- Corpos JSON (`Content-Type: application/json`), excepto o login e os envios de ficheiros (`multipart/form-data`).
- Datas `AAAA-MM-DD`; datas com hora em ISO 8601. Valores em kwanzas como números (`45000.0`).
- Cada resposta tem o modelo documentado em **Schemas**. Os tipos TypeScript podem ser gerados de
  `/api/openapi.json` (por exemplo com `npx openapi-typescript <url>/api/openapi.json -o api.d.ts`).
- Os PDFs e os ficheiros Excel vêm como binário: use o `Content-Disposition` para o nome do ficheiro.
- Contas de encarregado só usam as rotas `/api/portal/*`.

## Erros
Sempre `{"detail": "mensagem em português"}` (ou uma lista de erros de validação, código 422).
**401** sessão inválida · **403** sem permissão · **404** não existe neste estabelecimento · **409** conflito
(duplicado, turma cheia…) · **422** dados inválidos · **429** demasiadas tentativas de login ·
**500** erro interno, com `Referência: <id>` que aparece nos registos do servidor.
"""

TAGS = [
    {"name": "Autenticação e utilizadores", "description": "Login, sessão, senha, dois passos e contas da escola."},
    {"name": "Académico", "description": "Anos lectivos, trimestres, classes, turmas, disciplinas, professores, fecho do ano."},
    {"name": "Cadastro e matrículas", "description": "Alunos, encarregados, matrículas e visão 360° do aluno."},
    {"name": "Propinas e cobranças", "description": "Serviços, preços, cobranças, pagamentos, multas e descontos."},
    {"name": "Refeitório e serviços", "description": "Adesões, ementas e registo diário de refeições."},
    {"name": "Comunicação e regras", "description": "Mensagens, regras automáticas, modelos e comunicados."},
    {"name": "Pedagógico", "description": "Avaliações, notas, pautas, assiduidade e ocorrências."},
    {"name": "Painel, alertas e assistente", "description": "Indicadores, alertas, assistente, relatórios e auditoria."},
    {"name": "Integração Primavera", "description": "Estado da sincronização e divergências com o Primavera."},
    {"name": "Importação de dados", "description": "CSV do software anterior: alunos, encarregados, cobranças, pagamentos."},
    {"name": "Documentos e ficheiros", "description": "PDFs (recibo, extracto, boletim, pauta, declaração) e envio de ficheiros."},
    {"name": "Portal do encarregado", "description": "Tudo o que a conta de um encarregado pode ver e fazer."},
    {"name": "Sistema", "description": "Verificação de saúde da API e da base de dados."},
]

app = FastAPI(
    title="CLACS Escola API",
    version="1.2.0",
    description=DESCRICAO,
    openapi_tags=TAGS,
    docs_url="/api/docs", redoc_url="/api/redoc", openapi_url="/api/openapi.json",
    lifespan=ciclo_de_vida,
)

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


# Registado depois do middleware acima para ficar por fora dele: assim também as respostas de erro 500 levam os
# cabeçalhos CORS (sem eles, o browser mostra "blocked by CORS policy" em vez do erro verdadeiro).
origens = [o.strip().rstrip("/") for o in get_settings().cors_origins.split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origens, allow_credentials="*" not in origens,
                   allow_methods=["*"], allow_headers=["*"], expose_headers=["Content-Disposition", "X-2FA"])


for r in (auth, academico, cadastro, financeiro, refeitorio, comunicacao, pedagogico, painel, primavera,
          importacao, documentos, portal):
    app.include_router(r.router)
app.include_router(portal.extra)

# Respostas reais (dados de demonstração) como exemplo no /api/docs; gerado por scripts/gerar_exemplos.py.
EXEMPLOS_RESPOSTA = json.loads((Path(__file__).parent / "exemplos_respostas.json").read_text(encoding="utf-8"))


def openapi_com_exemplos():
    if app.openapi_schema is None:
        esquema = FastAPI.openapi(app)
        for chave, exemplo in EXEMPLOS_RESPOSTA.items():
            metodo, caminho = chave.split(" ", 1)
            operacao = esquema["paths"].get(caminho, {}).get(metodo.lower(), {"responses": {}})
            sucesso = next((operacao["responses"][c] for c in ("200", "201") if c in operacao["responses"]), None)
            if sucesso:
                sucesso["content"]["application/json"]["example"] = exemplo
    return app.openapi_schema


app.openapi = openapi_com_exemplos


@app.get("/", include_in_schema=False)
def raiz():
    # O frontend (Vite + React) está em clacs-frontend/ e é publicado à parte.
    return RedirectResponse("/api/docs")
