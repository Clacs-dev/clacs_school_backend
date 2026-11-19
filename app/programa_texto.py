"""Programa da disciplina em texto, como no programa oficial.

Cada unidade (ProgramaUnidade) tem um título e os conteúdos, um por linha; o nível de cada conteúdo indica-se com
traços no início: sem traço = 1.1, «-» = 1.1.1, «--» = 1.1.1.1. A numeração é sempre calculada.

O texto colado do programa oficial (ex.: «13.2.1.1. Deslocamentos na curva…») é lido para esse formato: o número
de partes do número dá o nível.
"""
import re

NUMERO = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+(.+)$")


def numerar_conteudos(conteudos: str | None, unidade: int) -> list[tuple[int, str, str]]:
    """(nível, número, texto) de cada conteúdo da unidade: nível 2 = 1.1., 3 = 1.1.1, 4 = 1.1.1.1."""
    linhas, contadores = [], [0, 0, 0]          # níveis 2, 3 e 4
    for bruta in (conteudos or "").splitlines():
        texto = bruta.strip()
        if not texto:
            continue
        tracos = len(texto) - len(texto.lstrip("-"))
        nivel = min(2 + tracos, 4)
        while nivel > 2 and not contadores[nivel - 3]:   # um subponto sem o ponto de cima sobe de nível
            nivel -= 1
        i = nivel - 2
        contadores[i] += 1
        for j in range(i + 1, 3):
            contadores[j] = 0
        partes = [str(unidade), *[str(c) for c in contadores[:i + 1]]]
        numero = ".".join(partes) + "."
        linhas.append((nivel, numero, texto.lstrip("- ").strip()))
    return linhas


def ler_programa(texto: str) -> list[tuple[str, str]]:
    """Lê o programa colado (numerado como no programa oficial) e devolve [(título da unidade, conteúdos)]."""
    unidades: list[list] = []
    pilha: list[int] = []                                # profundidades (do número) dos pontos acima, na unidade
    for bruta in (texto or "").splitlines():
        linha = bruta.strip().replace("\t", " ")
        if not linha or linha.upper() == "PROGRAMA":
            continue
        m = NUMERO.match(linha)
        if m:
            partes = [p for p in m.group(1).split(".") if p]
            texto_linha = re.sub(r"\s+", " ", m.group(2)).strip()
            if len(partes) == 1:
                unidades.append([texto_linha, []])
                pilha = []
                continue
            if not unidades:
                unidades.append(["Introdução", []])
            # O nível vem da estrutura (um número que salta um nível, ex.: 14.1.1 sem 14.1, fica no nível seguinte).
            while pilha and pilha[-1] >= len(partes):
                pilha.pop()
            tracos = min(len(pilha), 2)
            pilha.append(len(partes))
            unidades[-1][1].append(("-" * tracos + " " if tracos else "") + texto_linha)
        elif unidades and unidades[-1][1]:               # continuação da linha anterior
            unidades[-1][1][-1] += " " + linha
        elif unidades:
            unidades[-1][0] += " " + linha
        else:
            unidades.append([linha, []])
    return [(t.strip(), "\n".join(c)) for t, c in unidades]


def escrever_programa(unidades: list[tuple[str, str | None]]) -> str:
    """O programa em texto numerado (para editar no formulário da ficha)."""
    linhas = []
    for n, (titulo, conteudos) in enumerate(unidades, start=1):
        if linhas:
            linhas.append("")
        linhas.append(f"{n}. {titulo}")
        linhas += [f"{numero} {texto}" for _, numero, texto in numerar_conteudos(conteudos, n)]
    return "\n".join(linhas)
