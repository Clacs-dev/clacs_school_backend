"""Percentagem do levantamento de requisitos que a API cumpre, a partir de docs/requisitos-cobertura.md.

    python -m scripts.cobertura_requisitos

"sim" conta 1, "parcial" conta 0,5, "não" conta 0; "fora" não entra na conta.
"""
from collections import Counter, defaultdict
from pathlib import Path

TABELA = Path(__file__).resolve().parent.parent / "docs" / "requisitos-cobertura.md"
PESO = {"sim": 1.0, "parcial": 0.5, "não": 0.0}


def ler() -> list[tuple[str, str]]:
    linhas = []
    for linha in TABELA.read_text(encoding="utf-8").splitlines():
        partes = [p.strip() for p in linha.strip().strip("|").split("|")]
        if len(partes) >= 3 and partes[0][:1].isdigit():
            linhas.append((partes[0], partes[2]))
    return linhas


def main() -> None:
    linhas = ler()
    contagem = Counter(estado for _, estado in linhas)
    desconhecidos = set(contagem) - set(PESO) - {"fora"}
    if desconhecidos:
        raise SystemExit(f"Estados desconhecidos: {desconhecidos}")
    seccoes: dict[str, list[float]] = defaultdict(list)
    for id_, estado in linhas:
        if estado in PESO:
            seccoes[id_.split(".")[0]].append(PESO[estado])
    total = sum(sum(v) for v in seccoes.values())
    n = sum(len(v) for v in seccoes.values())
    for s, v in sorted(seccoes.items(), key=lambda x: int(x[0])):
        print(f"  secção {s:>2}: {sum(v) / len(v):6.1%} ({len(v)} requisitos)")
    print(f"{n} requisitos da API ({contagem['fora']} fora da API): sim {contagem['sim']}, "
          f"parcial {contagem['parcial']}, não {contagem['não']}")
    print(f"Cobertura: {total / n:.1%}")


if __name__ == "__main__":
    main()
