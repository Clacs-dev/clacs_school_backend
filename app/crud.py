"""CRUD completo para as entidades simples: ver um (GET /{id}), editar (PATCH /{id}) e apagar (DELETE /{id}).

Cada rota garante que o registo é do estabelecimento do utilizador (também pelas tabelas-mãe, ex.: a classe
pelo nível) e que os ids de outras tabelas recebidos num PATCH também o são. Apagar um registo ainda usado
noutro lado devolve 409 em vez de deixar dados órfãos.
"""
from typing import Annotated, Any, Callable, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import Field, create_model
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.common import aplicar, as_dict
from app.database import get_db
from app.models import (
    Aluno, AnoLectivo, Avaliacao, Base, Classe, Ementa, NivelEnsino, Ocorrencia, ParagemTransporte, PeriodoLectivo,
    RefeicaoTipo, RotaTransporte, Turma,
)
from app.schemas import EXEMPLOS, Entrada, com_exemplo
from app.security import Contexto, exige, turmas_permitidas

# Tabelas sem estabelecimento_id: o dono é o da tabela-mãe.
PAIS: dict[type, tuple[str, type]] = {
    Classe: ("nivel_id", NivelEnsino), PeriodoLectivo: ("ano_id", AnoLectivo), Ementa: ("refeicao_tipo_id", RefeicaoTipo),
    Avaliacao: ("turma_id", Turma), Ocorrencia: ("aluno_id", Aluno), ParagemTransporte: ("rota_id", RotaTransporte),
}
_POR_TABELA = {m.class_.__tablename__: m.class_ for m in Base.registry.mappers}


def estabelecimento_de(db: Session, obj: Any) -> int | None:
    if obj is None:
        return None
    if hasattr(obj, "estabelecimento_id"):
        return obj.estabelecimento_id
    if type(obj) in PAIS:
        campo, mae = PAIS[type(obj)]
        return estabelecimento_de(db, db.get(mae, getattr(obj, campo)))
    return None


def _carregar(db: Session, modelo: type, id_: int, est: int, nome: str):
    obj = db.get(modelo, id_)
    if obj is None or estabelecimento_de(db, obj) != est:
        raise HTTPException(404, f"{nome} {id_} não encontrado")
    return obj


def _validar_referencias(db: Session, modelo: type, dados: dict, est: int) -> None:
    """Um PATCH não pode apontar para registos (turma, serviço, classe…) de outro estabelecimento."""
    for coluna in modelo.__table__.columns:
        valor = dados.get(coluna.name)
        if valor is None or not coluna.foreign_keys:
            continue
        alvo = _POR_TABELA.get(next(iter(coluna.foreign_keys)).column.table.name)
        if alvo is None or alvo.__tablename__ == "estabelecimento":
            continue
        dono = estabelecimento_de(db, db.get(alvo, valor))
        if dono is not None and dono != est or db.get(alvo, valor) is None:
            raise HTTPException(422, f"{coluna.name} {valor} não existe")


def modelo_de_edicao(entrada: type[Entrada], nome: str, excluir: tuple[str, ...] = ()) -> type[Entrada]:
    """Versão com todos os campos opcionais do esquema de criação (as validações de cada campo mantêm-se)."""
    campos = {}
    for n, f in entrada.model_fields.items():
        if n in excluir:
            continue
        tipo = Annotated[f.annotation, *f.metadata] if f.metadata else f.annotation
        campos[n] = (Optional[tipo], Field(None, description=f.description))
    upd = create_model(nome, __base__=Entrada, **campos)
    exemplo = {k: v for k, v in EXEMPLOS.get(entrada, {}).items() if k in campos}
    return com_exemplo(exemplo)(upd) if exemplo else upd


def registar(router: APIRouter, caminho: str, modelo: type, *, nome: str, modulo: str, resposta: type,
             entrada: type[Entrada] | None = None, excluir: tuple[str, ...] = (), ops: tuple[str, ...] = ("ver", "editar", "apagar"),
             verificar: Callable[[Session, Contexto, Any], None] | None = None) -> None:
    """Acrescenta ao router as rotas que faltam: "ver" (GET), "editar" (PATCH) e/ou "apagar" (DELETE)."""
    rota = f"{caminho}/{{id_}}"
    tag = router.tags[0] if router.tags else None

    def carregar(db: Session, ctx: Contexto, id_: int):
        obj = _carregar(db, modelo, id_, ctx.estabelecimento_id, nome)
        if verificar:
            verificar(db, ctx, obj)
        return obj

    if "ver" in ops:
        def ver(id_: int, ctx: Contexto = Depends(exige(modulo)), db: Session = Depends(get_db)):
            return as_dict(carregar(db, ctx, id_))
        router.add_api_route(rota, ver, methods=["GET"], response_model=resposta, summary=f"Ver {nome.lower()}",
                             name=f"ver_{modelo.__tablename__}", tags=[tag] if tag else None)

    if "editar" in ops:
        upd = modelo_de_edicao(entrada, f"{entrada.__name__.removesuffix('In')}Edicao", excluir)
        colunas = {c.name for c in modelo.__table__.columns} - {"id", "estabelecimento_id"}

        def editar(id_: int, dados: upd, ctx: Contexto = Depends(exige(modulo, "editar")),  # type: ignore[valid-type]
                   db: Session = Depends(get_db)):
            obj = carregar(db, ctx, id_)
            mudancas = {k: v for k, v in dados.model_dump(exclude_unset=True).items() if k in colunas}
            _validar_referencias(db, modelo, mudancas, ctx.estabelecimento_id)
            aplicar(obj, mudancas)
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                raise HTTPException(409, f"Já existe {nome.lower()} com estes dados")
            return as_dict(obj)
        router.add_api_route(rota, editar, methods=["PATCH"], response_model=resposta, summary=f"Editar {nome.lower()}",
                             name=f"editar_{modelo.__tablename__}", tags=[tag] if tag else None)

    if "apagar" in ops:
        def apagar(id_: int, ctx: Contexto = Depends(exige(modulo, "apagar")), db: Session = Depends(get_db)):
            obj = carregar(db, ctx, id_)
            try:
                db.delete(obj)
                db.commit()
            except IntegrityError:
                db.rollback()
                dica = " Desactive-o em vez de apagar." if hasattr(obj, "activo") else ""
                raise HTTPException(409, f"Não é possível apagar: {nome.lower()} {id_} ainda está a ser usado.{dica}")
            return Response(status_code=204)
        router.add_api_route(rota, apagar, methods=["DELETE"], status_code=204, response_class=Response,
                             summary=f"Apagar {nome.lower()} (recusa se ainda estiver a ser usado)",
                             name=f"apagar_{modelo.__tablename__}", tags=[tag] if tag else None)


def so_turmas_permitidas(db: Session, ctx: Contexto, obj: Any) -> None:
    """Professores só mexem nas avaliações das turmas onde dão aulas."""
    permitidas = turmas_permitidas(db, ctx)
    if permitidas is not None and obj.turma_id not in permitidas:
        raise HTTPException(403, "Sem acesso a esta turma")
