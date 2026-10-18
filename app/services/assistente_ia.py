"""Assistente em linguagem natural com a API da Claude (opcional: precisa de ANTHROPIC_API_KEY).

O modelo não escreve SQL nem vê a base de dados: só pode chamar as ferramentas abaixo, que são consultas de leitura
já existentes e ficam sempre presas ao estabelecimento de quem pergunta. Sem chave, ou se a API falhar, quem chama
usa o assistente por regras (app/routers/painel.py::responder).
"""
import json
import logging
from datetime import date

import anthropic
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.common import ano_activo
from app.config import get_settings
from app.models import Aluno, Matricula, Pagamento, Turma
from app.services import financeiro as fin
from app.services.alertas import calcular_alertas, comportamento_actual
from app.services.pedagogico import media_geral, total_faltas

log = logging.getLogger("clacs.assistente")
MAX_PASSOS = 6

SISTEMA = """És o assistente da Direcção do Colégio Cota Lionel (Angola), dentro do sistema CLACS Escola.
Respondes em português de Angola, em uma a três frases curtas, com os números exactos que as ferramentas devolverem.
Valores em kwanzas escrevem-se como "45 000 Kz". Datas como dd/mm/aaaa.
Usa sempre as ferramentas para obter dados; nunca inventes números, nomes ou turmas.
Se as ferramentas não tiverem a informação pedida, diz o que podes responder (cobranças, dívidas, turmas, alunos,
refeitório, alertas, valores recebidos)."""


def _s(propriedades: dict) -> dict:
    """Esquema estrito: todas as propriedades obrigatórias, opcionais como tipo ["...", "null"]."""
    return {"type": "object", "properties": propriedades, "required": list(propriedades), "additionalProperties": False}


TEXTO_OU_NULO = {"type": ["string", "null"]}
FERRAMENTAS = [
    {"name": "resumo_cobrancas", "strict": True,
     "description": "Totais de cobranças: facturado, recebido, em aberto, em atraso, alunos em incumprimento e taxa de "
                    "cobrança. Filtros opcionais: turma (ex.: '7.ª A'), categoria e estado.",
     "input_schema": _s({
         "turma": {**TEXTO_OU_NULO, "description": "Designação da turma, ou null para todas"},
         "categoria": {"type": ["string", "null"], "enum": ["propina", "alimentacao", "refeitorio", "matricula",
                                                            "fardamento", "transporte", "actividade", "outro", None]},
         "estado": {"type": ["string", "null"], "enum": ["pago", "divida", "atraso", None]}})},
    {"name": "devedores", "strict": True,
     "description": "Alunos com cobranças vencidas por pagar, do maior para o menor valor em dívida.",
     "input_schema": _s({"limite": {"type": "integer", "description": "Quantos devolver (1 a 50)"}})},
    {"name": "cobranca_por_turma", "strict": True,
     "description": "Por turma: alunos, facturado, recebido, em atraso e taxa de cobrança.",
     "input_schema": _s({})},
    {"name": "contar_alunos", "strict": True,
     "description": "Número de alunos matriculados no ano activo, no total ou numa turma.",
     "input_schema": _s({"turma": TEXTO_OU_NULO})},
    {"name": "procurar_aluno", "strict": True,
     "description": "Procura alunos pelo nome e devolve, para cada um (até 3): turma, encarregado, dívida, média, faltas "
                    "e comportamento.",
     "input_schema": _s({"nome": {"type": "string"}})},
    {"name": "refeitorio", "strict": True,
     "description": "Quantos alunos pagam alimentação, quantos pagam só a utilização e quanto está em atraso no refeitório.",
     "input_schema": _s({})},
    {"name": "alertas", "strict": True,
     "description": "Alertas actuais: risco de incumprimento, queda de desempenho, faltas, comportamento, Primavera.",
     "input_schema": _s({})},
    {"name": "recebido", "strict": True,
     "description": "Total de pagamentos confirmados entre duas datas (AAAA-MM-DD); sem datas, o mês corrente.",
     "input_schema": _s({"desde": TEXTO_OU_NULO, "ate": TEXTO_OU_NULO})},
]


def _turma_id(db: Session, est: int, nome: str | None) -> int | None:
    if not nome:
        return None
    ano = ano_activo(db, est, obrigatorio=False)
    alvo = nome.replace(" ", "").replace(".", "").replace("ª", "").lower()
    for t in db.scalars(select(Turma).where(Turma.estabelecimento_id == est, Turma.ano_id == (ano.id if ano else -1))):
        if t.designacao.replace(" ", "").replace(".", "").replace("ª", "").lower() == alvo:
            return t.id
    raise ValueError(f"turma '{nome}' não existe no ano activo")


def executar(db: Session, est: int, nome: str, args: dict):
    """Executa uma ferramenta de leitura para o estabelecimento `est`. Devolve dados serializáveis."""
    if nome == "resumo_cobrancas":
        linhas = fin.cobrancas_detalhe(db, est, turma_id=_turma_id(db, est, args.get("turma")),
                                       categoria=args.get("categoria"), estado=args.get("estado"))
        return fin.resumo(linhas)
    if nome == "devedores":
        return fin.devedores(db, est)[: max(1, min(50, int(args.get("limite") or 10)))]
    if nome == "cobranca_por_turma":
        return fin.resumo_por_turma(db, est)
    if nome == "contar_alunos":
        ano = ano_activo(db, est, obrigatorio=False)
        stmt = select(func.count()).select_from(Matricula).where(
            Matricula.estabelecimento_id == est, Matricula.ano_id == (ano.id if ano else -1), Matricula.estado == "activa")
        tid = _turma_id(db, est, args.get("turma"))
        if tid:
            stmt = stmt.where(Matricula.turma_id == tid)
        return {"alunos": db.scalar(stmt), "ano_lectivo": ano.designacao if ano else None}
    if nome == "procurar_aluno":
        res = []
        for a in db.scalars(select(Aluno).where(Aluno.estabelecimento_id == est,
                                                Aluno.nome_completo.ilike(f"%{args['nome'].strip()}%")).limit(3)):
            m = db.scalar(select(Matricula).where(Matricula.aluno_id == a.id, Matricula.estado == "activa"))
            cob = fin.cobrancas_detalhe(db, est, aluno_id=a.id, estado="divida")
            res.append({"nome": a.nome_completo, "processo": a.numero_processo,
                        "turma": db.get(Turma, m.turma_id).designacao if m else None,
                        "encarregado": cob[0]["encarregado"] if cob else None,
                        "em_divida_kz": sum(c["valor_em_aberto"] for c in cob),
                        "media": media_geral(db, m) if m else None, "faltas": total_faltas(db, a.id),
                        "comportamento": comportamento_actual(db, a.id)})
        return res or {"aviso": "nenhum aluno encontrado com esse nome"}
    if nome == "refeitorio":
        linhas = fin.cobrancas_detalhe(db, est)
        return {"alunos_alimentacao": len({x["aluno_id"] for x in linhas if x["categoria"] == "alimentacao"}),
                "alunos_so_utilizacao": len({x["aluno_id"] for x in linhas if x["categoria"] == "refeitorio"}),
                "em_atraso_kz": sum(x["valor_em_aberto"] for x in linhas
                                    if x["categoria"] in ("alimentacao", "refeitorio") and x["dias_atraso"] > 0)}
    if nome == "alertas":
        return [{"tipo": a["tipo"], "mensagem": a["mensagem"]} for a in calcular_alertas(db, est)]
    if nome == "recebido":
        hoje = date.today()
        desde = date.fromisoformat(args["desde"]) if args.get("desde") else hoje.replace(day=1)
        ate = date.fromisoformat(args["ate"]) if args.get("ate") else hoje
        v = db.scalar(select(func.coalesce(func.sum(Pagamento.valor), 0)).where(
            Pagamento.estabelecimento_id == est, Pagamento.estado == "confirmado",
            Pagamento.data_pagamento >= desde, Pagamento.data_pagamento <= ate))
        return {"desde": desde, "ate": ate, "recebido_kz": float(v)}
    raise ValueError(f"ferramenta desconhecida: {nome}")


def disponivel() -> bool:
    return bool(get_settings().anthropic_api_key)


def responder(db: Session, est: int, pergunta: str, cliente: anthropic.Anthropic | None = None) -> tuple[str, list]:
    """Devolve (resposta, chamadas feitas). Lança anthropic.APIError se a API falhar."""
    s = get_settings()
    cliente = cliente or anthropic.Anthropic(api_key=s.anthropic_api_key, timeout=60.0)
    mensagens = [{"role": "user", "content": pergunta}]
    chamadas: list[dict] = []
    for _ in range(MAX_PASSOS):
        r = cliente.beta.messages.create(
            model=s.assistente_modelo, max_tokens=4000, system=SISTEMA, tools=FERRAMENTAS, messages=mensagens,
            output_config={"effort": "low"},
            # Se o modelo recusar por segurança, a API tenta outro modelo na mesma chamada.
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        )
        if r.stop_reason == "refusal":
            return "Não posso responder a essa pergunta.", chamadas
        usos = [b for b in r.content if b.type == "tool_use"]
        if r.stop_reason != "tool_use" or not usos:
            texto = " ".join(b.text for b in r.content if b.type == "text").strip()
            return texto or "Não consegui obter uma resposta.", chamadas
        mensagens.append({"role": "assistant", "content": r.content})
        resultados = []
        for u in usos:
            entrada = u.input if isinstance(u.input, dict) else {}
            try:
                dados = executar(db, est, u.name, entrada)
                resultados.append({"type": "tool_result", "tool_use_id": u.id,
                                   "content": json.dumps(dados, default=str, ensure_ascii=False)})
            except (ValueError, KeyError, TypeError) as e:
                resultados.append({"type": "tool_result", "tool_use_id": u.id, "content": str(e), "is_error": True})
            chamadas.append({"ferramenta": u.name, "entrada": entrada})
        mensagens.append({"role": "user", "content": resultados})
    return "A pergunta precisou de demasiados passos. Tente ser mais específico.", chamadas
