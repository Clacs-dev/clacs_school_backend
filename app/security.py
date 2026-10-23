from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import AuditoriaLog, Perfil, PerfilPermissao, Permissao, Utilizador, UtilizadorPerfil

oauth2 = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

MODULOS = ("painel", "cadastro", "academico", "propinas", "refeitorio", "pedagogico",
           "comunicacao", "primavera", "utilizadores", "importacao", "transporte")
ACCOES = ("ver", "criar", "editar", "apagar", "exportar")
TUDO = {m: ACCOES for m in MODULOS}

# Matriz inicial de permissões por perfil (editável depois nas tabelas perfil_permissao).
MATRIZ: dict[str, dict[str, tuple[str, ...]]] = {
    "admin": TUDO,
    "direccao": TUDO,
    "secretaria": {
        "painel": ("ver",), "cadastro": ACCOES, "academico": ACCOES, "refeitorio": ACCOES,
        "propinas": ("ver", "criar", "exportar"), "comunicacao": ("ver", "criar"), "importacao": ACCOES,
        "transporte": ACCOES,
    },
    "financeiro": {
        "painel": ("ver",), "propinas": ACCOES, "refeitorio": ("ver", "exportar"), "cadastro": ("ver",),
        "primavera": ACCOES, "comunicacao": ("ver", "criar"), "transporte": ("ver", "exportar"),
    },
    "coordenacao": {
        "painel": ("ver",), "pedagogico": ACCOES, "cadastro": ("ver",),
        "academico": ("ver", "criar", "editar"), "comunicacao": ("ver", "criar"),
    },
    "professor": {"pedagogico": ("ver", "criar", "editar"), "cadastro": ("ver",), "academico": ("ver",)},
    "encarregado": {},
}


def hash_senha(senha: str) -> str:
    return bcrypt.hashpw(senha.encode(), bcrypt.gensalt()).decode()


def verificar_senha(senha: str, senha_hash: str) -> bool:
    try:
        return bcrypt.checkpw(senha.encode(), senha_hash.encode())
    except ValueError:
        return False


def criar_token(utilizador_id: int, estabelecimento_id: int) -> str:
    s = get_settings()
    agora = datetime.now(timezone.utc)
    payload = {"sub": str(utilizador_id), "est": estabelecimento_id, "iat": agora,
               "exp": agora + timedelta(minutes=s.jwt_expira_minutos)}
    return jwt.encode(payload, s.jwt_secret, algorithm="HS256")


@dataclass
class Contexto:
    """Utilizador autenticado e o estabelecimento em que está a trabalhar."""
    utilizador: Utilizador
    estabelecimento_id: int
    perfis: set[str] = field(default_factory=set)
    permissoes: set[tuple[str, str]] = field(default_factory=set)

    @property
    def id(self) -> int:
        return self.utilizador.id

    def pode(self, modulo: str, accao: str) -> bool:
        return "admin" in self.perfis or (modulo, accao) in self.permissoes


def carregar_contexto(db: Session, utilizador: Utilizador, estabelecimento_id: int) -> Contexto:
    perfis = set(db.scalars(
        select(Perfil.codigo).join(UtilizadorPerfil, UtilizadorPerfil.perfil_id == Perfil.id)
        .where(UtilizadorPerfil.utilizador_id == utilizador.id,
               UtilizadorPerfil.estabelecimento_id == estabelecimento_id)))
    perms = set(db.execute(
        select(Permissao.modulo, Permissao.accao)
        .join(PerfilPermissao, PerfilPermissao.permissao_id == Permissao.id)
        .join(Perfil, Perfil.id == PerfilPermissao.perfil_id)
        .where(Perfil.codigo.in_(perfis))).tuples())
    return Contexto(utilizador, estabelecimento_id, perfis, perms)


def contexto_actual(token: str = Depends(oauth2), db: Session = Depends(get_db)) -> Contexto:
    erro = HTTPException(status.HTTP_401_UNAUTHORIZED, "Sessão inválida ou expirada",
                         headers={"WWW-Authenticate": "Bearer"})
    try:
        payload = jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])
        uid, est = int(payload["sub"]), int(payload["est"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise erro
    u = db.get(Utilizador, uid)
    if not u or not u.activo:
        raise erro
    ctx = carregar_contexto(db, u, est)
    if not ctx.perfis:
        raise erro
    return ctx


def exige(modulo: str, accao: str = "ver"):
    def dep(ctx: Contexto = Depends(contexto_actual)) -> Contexto:
        if not ctx.pode(modulo, accao):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Sem permissão: {modulo}/{accao}")
        return ctx
    return dep


def auditar(db: Session, ctx: Contexto | None, tabela: str, registo_id: int | None, accao: str,
            depois: dict | None = None, antes: dict | None = None, estabelecimento_id: int | None = None):
    db.add(AuditoriaLog(
        estabelecimento_id=estabelecimento_id or (ctx.estabelecimento_id if ctx else None),
        utilizador_id=ctx.id if ctx else None, tabela=tabela, registo_id=registo_id, accao=accao,
        dados_antes=antes, dados_depois=depois))


# ---------------------------------------------------------------- professor: só as suas turmas
PERFIS_SEM_RESTRICAO = {"admin", "direccao", "coordenacao", "secretaria", "financeiro"}


def turmas_permitidas(db: Session, ctx: Contexto) -> set[int] | None:
    """None = todas. Um utilizador só com o perfil professor vê as turmas onde lecciona ou é director de turma."""
    if ctx.perfis & PERFIS_SEM_RESTRICAO or "professor" not in ctx.perfis:
        return None
    fid = ctx.utilizador.funcionario_id
    if not fid:
        return set()
    from app.models import AtribuicaoDocente, Turma
    leccionadas = set(db.scalars(select(AtribuicaoDocente.turma_id).where(AtribuicaoDocente.funcionario_id == fid)))
    dirigidas = set(db.scalars(select(Turma.id).where(Turma.director_turma_id == fid,
                                                      Turma.estabelecimento_id == ctx.estabelecimento_id)))
    return leccionadas | dirigidas


def verificar_turma(db: Session, ctx: Contexto, turma_id: int) -> None:
    permitidas = turmas_permitidas(db, ctx)
    if permitidas is not None and turma_id not in permitidas:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Sem acesso a esta turma")
