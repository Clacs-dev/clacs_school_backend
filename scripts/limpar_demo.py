"""Apaga os dados de demonstração (e de testes) antes de a escola começar a usar o sistema.

    python -m scripts.limpar_demo              mostra o que seria apagado (não muda nada)
    python -m scripts.limpar_demo --confirmar  apaga
    ... --manter outro@email.ao                 mantém também esta conta (pode repetir)

Fica a configuração neutra: a escola, os perfis e permissões, o administrador (ADMIN_EMAIL), os métodos de
pagamento, os tipos de ocorrência e de avaliação, os tipos de refeição, os parâmetros (escala de notas…), os
indicadores do painel e a configuração da integração. Sai tudo o resto: alunos, encarregados, turmas, classes,
disciplinas, salas, funcionários, anos lectivos, serviços e preços, cobranças e pagamentos, notas, mensagens,
transporte, auditoria e as outras contas (professor@ e encarregado@ de demonstração incluídas).

Usa a DATABASE_URL do ambiente: para a produção, DATABASE_URL=<External Database URL do Render>.
Atenção: com SEED_DEMO=true o próximo arranque volta a carregar a demonstração (a base fica sem alunos).
"""
import argparse
import shutil

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.armazenamento import raiz
from app.config import get_settings
from app.database import SessionLocal
from app.models import Base, Utilizador, UtilizadorPerfil

MANTER = {
    "estabelecimento", "perfil", "permissao", "perfil_permissao", "metodo_pagamento", "tipo_ocorrencia",
    "tipo_avaliacao", "refeicao_tipo", "parametro", "indicador", "integracao_config",
}
# Só fica a linha do administrador.
FILTRADAS = {"utilizador", "utilizador_perfil"}


def _verificar_dependencias() -> None:
    """Uma tabela que fica não pode apontar para uma que se esvazia (ficaria com referências partidas)."""
    for t in Base.metadata.sorted_tables:
        if t.name not in MANTER:
            continue
        for fk in t.foreign_keys:
            alvo = fk.column.table.name
            if alvo not in MANTER and alvo not in FILTRADAS:
                raise SystemExit(f"{t.name}.{fk.parent.name} aponta para {alvo}, que seria apagada")


def limpar(db: Session, confirmar: bool, ficheiros: bool = True, manter: tuple[str, ...] = ()) -> dict[str, int]:
    _verificar_dependencias()
    admin = db.scalar(select(Utilizador).where(func.lower(Utilizador.email) == get_settings().admin_email.lower()))
    if admin is None:
        raise SystemExit(f"Administrador {get_settings().admin_email} não encontrado: nada foi apagado")
    contas_mantidas = list(db.scalars(select(Utilizador).where(
        func.lower(Utilizador.email).in_([admin.email.lower(), *(e.lower() for e in manter)]))))
    if len(contas_mantidas) != 1 + len(set(e.lower() for e in manter) - {admin.email.lower()}):
        raise SystemExit("Uma das contas de --manter não existe: nada foi apagado")
    ids = [u.id for u in contas_mantidas]
    contas: dict[str, int] = {}
    # Das tabelas-filhas para as tabelas-mãe, para respeitar as chaves estrangeiras.
    for t in reversed(Base.metadata.sorted_tables):
        if t.name in MANTER:
            continue
        cond = None
        if t.name == "utilizador":
            cond = t.c.id.not_in(ids)
        elif t.name == "utilizador_perfil":
            cond = t.c.utilizador_id.not_in(ids)
        n = db.scalar(select(func.count()).select_from(t).where(*([cond] if cond is not None else [])))
        if n:
            contas[t.name] = n
            if confirmar:
                db.execute(delete(t).where(cond) if cond is not None else delete(t))
    if confirmar:
        # o administrador deixa de estar ligado a um funcionário/encarregado que já não existe
        for u in contas_mantidas:
            u.funcionario_id = u.encarregado_id = None
            if ficheiros:
                u.foto_url = None  # a fotografia vai com os ficheiros
        db.commit()
    else:
        db.rollback()
    return contas


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--confirmar", action="store_true", help="apaga mesmo (sem isto só mostra o que seria apagado)")
    ap.add_argument("--manter", action="append", default=[], metavar="EMAIL", help="conta a manter além do administrador")
    ap.add_argument("--manter-ficheiros", action="store_true", help="não apaga os ficheiros enviados (UPLOAD_DIR)")
    args = ap.parse_args()
    url = get_settings().database_url
    print(f"Base: {url.split('@')[-1] if '@' in url else url}")  # sem a senha
    with SessionLocal() as db:
        contas = limpar(db, args.confirmar, not args.manter_ficheiros, tuple(args.manter))
    for nome, n in sorted(contas.items()):
        print(f"  {nome:28} {n:6}")
    print(f"Total: {sum(contas.values())} registos em {len(contas)} tabelas.")
    if not args.confirmar:
        print("Nada foi apagado. Corra de novo com --confirmar para apagar.")
        return
    if not args.manter_ficheiros:
        for pasta in raiz().iterdir():
            if pasta.is_dir():
                shutil.rmtree(pasta)
    print("Dados de demonstração apagados.")


if __name__ == "__main__":
    main()
