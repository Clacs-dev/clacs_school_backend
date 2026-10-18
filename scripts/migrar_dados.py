"""Copia todos os dados de uma base para outra, por exemplo do SQLite/PostgreSQL local para o PostgreSQL do Render.

    python -m scripts.migrar_dados --origem sqlite:///./clacs.db --destino "postgresql://...(External Database URL)..."

O esquema do destino é criado/actualizado com o Alembic antes da cópia. Por segurança, a cópia é recusada se o
destino já tiver dados, a não ser com --limpar (apaga TUDO no destino).
"""
import argparse
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import func, insert, select, text

from app.config import normalizar_url
from app.database import make_engine
from app.models import Base

RAIZ = Path(__file__).resolve().parent.parent
LOTE = 1000


def migrar_esquema(url: str) -> None:
    cfg = Config(str(RAIZ / "alembic.ini"))
    cfg.set_main_option("script_location", str(RAIZ / "alembic"))
    cfg.attributes["url"] = url
    command.upgrade(cfg, "head")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--origem", required=True)
    ap.add_argument("--destino", required=True)
    ap.add_argument("--limpar", action="store_true", help="apaga os dados existentes no destino antes de copiar")
    args = ap.parse_args()
    origem, destino = normalizar_url(args.origem), normalizar_url(args.destino)
    if origem == destino:
        raise SystemExit("A origem e o destino são a mesma base.")

    print("1/4 A preparar o esquema no destino (alembic upgrade head)…")
    migrar_esquema(destino)
    src, dst = make_engine(origem), make_engine(destino)
    tabelas = Base.metadata.sorted_tables

    with dst.begin() as d:
        ocupadas = [t.name for t in tabelas if d.scalar(select(func.count()).select_from(t))]
        if ocupadas and not args.limpar:
            raise SystemExit(f"O destino já tem dados ({', '.join(ocupadas[:5])}…). Use --limpar para os apagar.")
        if ocupadas:
            print("2/4 A limpar o destino…")
            if dst.dialect.name == "postgresql":
                d.execute(text("TRUNCATE " + ", ".join(f'"{t.name}"' for t in tabelas) + " RESTART IDENTITY CASCADE"))
            else:
                for t in reversed(tabelas):
                    d.execute(t.delete())

    print("3/4 A copiar os dados…")
    total = 0
    with src.connect() as s, dst.begin() as d:
        for t in tabelas:
            colunas = [c for c in t.columns if c.computed is None]  # valor_total é calculado pela base
            n = 0
            resultado = s.execute(select(*colunas)).mappings()
            while lote := resultado.fetchmany(LOTE):
                d.execute(insert(t), [dict(r) for r in lote])
                n += len(lote)
            if n:
                print(f"   {t.name}: {n}")
            total += n

        if dst.dialect.name == "postgresql":
            print("4/4 A acertar as sequências de identidade…")
            for t in tabelas:
                if "id" in t.columns and t.columns["id"].primary_key:
                    d.execute(text(f"SELECT setval(pg_get_serial_sequence('\"{t.name}\"', 'id'), "
                                   f"COALESCE(MAX(id), 1), MAX(id) IS NOT NULL) FROM \"{t.name}\""))
    print(f"Concluído: {total} registos copiados.")


if __name__ == "__main__":
    main()
