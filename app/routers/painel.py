import json
import logging
import re
from datetime import date, datetime, timezone

import anthropic
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import agendador
from app import crud
from app import respostas as R
from app.common import ano_activo, as_dict, obter, primeiro_dia, soma_meses
from app.database import get_db
from app.exportar import tabela
from app.integracoes import ErroIntegracao
from app.models import (
    Alerta, Aluno, AuditoriaLog, AvaliacaoProfessor, Funcionario, IaConsultaLog, Indicador, Matricula, Pagamento,
    PeriodoLectivo, Perfil, RelatorioAgendado, RelatorioExecucao, Turma, Utilizador,
)
from app.schemas import Entrada, EstadoIn, PerguntaIn, RelatorioIn, com_exemplo
from app.security import Contexto, exige
from app.services import financeiro as fin
from app.services import assistente_ia, indicadores, relatorios
from app.services.alertas import calcular_alertas, persistir_alertas
from app.services.comunicacao import kz

router = APIRouter(prefix="/api", tags=["Painel, alertas e assistente"])


@router.get("/painel", response_model=R.Painel, summary="Painel da Direcção")
def painel(ctx: Contexto = Depends(exige("painel")), db: Session = Depends(get_db)):
    est = ctx.estabelecimento_id
    hoje = date.today()
    mes = primeiro_dia(hoje)
    linhas = fin.cobrancas_detalhe(db, est, ate_competencia=mes)
    r = fin.resumo(linhas)
    recebido_mes = db.scalar(select(func.coalesce(func.sum(Pagamento.valor), 0)).where(
        Pagamento.estabelecimento_id == est, Pagamento.estado == "confirmado",
        Pagamento.data_pagamento >= mes, Pagamento.data_pagamento < soma_meses(mes, 1)))
    ano = ano_activo(db, est, obrigatorio=False)
    alunos = db.scalar(select(func.count()).select_from(Matricula).where(
        Matricula.ano_id == (ano.id if ano else -1), Matricula.estado == "activa")) or 0
    return {
        "data": hoje, "ano_lectivo": ano.designacao if ano else None,
        "kpis": {"recebido_mes": float(recebido_mes), "em_atraso": r["em_atraso"],
                 "alunos_em_incumprimento": r["alunos_em_incumprimento"], "alunos": alunos,
                 "taxa_cobranca": r["taxa_cobranca"], "facturado": r["facturado"], "recebido": r["recebido"]},
        "indicadores": indicadores.visiveis(db, est, ctx.perfis),
        "por_turma": fin.resumo_por_turma(db, est, ate_competencia=mes),
        "alertas": calcular_alertas(db, est, hoje),
    }


# ---------------------------------------------------------------- indicadores configuráveis
@com_exemplo({"codigo": "alunos_matriculados", "nome": "Alunos matriculados", "perfil_minimo": "direccao"})
class IndicadorIn(Entrada):
    codigo: str
    nome: str | None = None
    perfil_minimo: str = "direccao"


@router.get("/indicadores/catalogo", response_model=list[R.IndicadorDoCatalogo], summary="Indicadores que o sistema sabe calcular")
def catalogo(ctx: Contexto = Depends(exige("painel"))):
    return [{"codigo": c, "nome": d.nome, "formato": d.formato, "fonte": d.fonte} for c, d in indicadores.CATALOGO.items()]


@router.get("/indicadores", response_model=list[R.IndicadorComValor], summary="Indicadores do painel visíveis para o utilizador, com o valor de hoje")
def listar_indicadores(ctx: Contexto = Depends(exige("painel")), db: Session = Depends(get_db)):
    return indicadores.visiveis(db, ctx.estabelecimento_id, ctx.perfis)


@router.post("/indicadores", response_model=R.Indicador, status_code=201, summary="Acrescenta um indicador ao painel")
def criar_indicador(dados: IndicadorIn, ctx: Contexto = Depends(exige("painel", "editar")), db: Session = Depends(get_db)):
    d = indicadores.CATALOGO.get(dados.codigo)
    if not d:
        raise HTTPException(422, f"Código desconhecido. Válidos: {', '.join(indicadores.CATALOGO)}")
    if not db.scalar(select(Perfil.id).where(Perfil.codigo == dados.perfil_minimo)):
        raise HTTPException(422, "Perfil desconhecido")
    if db.scalar(select(Indicador.id).where(Indicador.estabelecimento_id == ctx.estabelecimento_id,
                                            Indicador.codigo == dados.codigo)):
        raise HTTPException(409, "Este indicador já está no painel")
    ind = Indicador(estabelecimento_id=ctx.estabelecimento_id, codigo=dados.codigo, nome=dados.nome or d.nome,
                    vista_origem=d.fonte, perfil_minimo=dados.perfil_minimo)
    db.add(ind)
    db.commit()
    return as_dict(ind)


@router.delete("/indicadores/{iid}", status_code=204, summary="Retira um indicador do painel")
def apagar_indicador(iid: int, ctx: Contexto = Depends(exige("painel", "editar")), db: Session = Depends(get_db)):
    ind = obter(db, Indicador, iid, ctx.estabelecimento_id, "Indicador")
    db.delete(ind)
    db.commit()


# ---------------------------------------------------------------- alertas
@router.get("/alertas", response_model=list[R.AlertaComAluno])
def alertas(estado: str | None = None, tipo: str | None = None, ctx: Contexto = Depends(exige("painel")),
            db: Session = Depends(get_db)):
    """tipo=comprovativo_pagamento: comprovativos enviados pelos encarregados à espera de verificação."""
    stmt = (select(Alerta, Aluno.nome_completo).outerjoin(Aluno, Aluno.id == Alerta.aluno_id)
            .where(Alerta.estabelecimento_id == ctx.estabelecimento_id))
    if tipo:
        stmt = stmt.where(Alerta.tipo == tipo)
    if estado:
        stmt = stmt.where(Alerta.estado == estado)
    return [{**as_dict(a), "aluno": n} for a, n in db.execute(stmt.order_by(Alerta.criado_em.desc()).limit(500))]


@router.post("/alertas/gerar", response_model=R.Novos, summary="Calcula e grava os alertas novos (para correr diariamente)")
def gerar_alertas(ctx: Contexto = Depends(exige("painel")), db: Session = Depends(get_db)):
    return {"novos": persistir_alertas(db, ctx.estabelecimento_id)}


@router.patch("/alertas/{aid}", response_model=R.Alerta)
def tratar_alerta(aid: int, dados: EstadoIn, ctx: Contexto = Depends(exige("painel")), db: Session = Depends(get_db)):
    a = obter(db, Alerta, aid, ctx.estabelecimento_id, "Alerta")
    a.estado = dados.estado
    if dados.estado in ("tratado", "descartado"):
        a.tratado_por = ctx.id
    db.commit()
    return as_dict(a)


# ---------------------------------------------------------------- assistente
def _turmas_citadas(pergunta: str, turmas: list[Turma]) -> list[Turma]:
    achadas = []
    for num, letra in re.findall(r"\b(\d{1,2})\s*\.?\s*[ªº]?\s*(?:classe)?\s*([A-Z])?\b", pergunta):
        for t in turmas:
            m = re.match(r"(\d+)\D*?([A-Z])?$", t.designacao.strip())
            if m and m.group(1) == num and (not letra or m.group(2) == letra):
                achadas.append(t)
    return list({t.id: t for t in achadas}.values())


def responder(db: Session, est: int, pergunta: str) -> str:
    q = pergunta.lower()
    ano = ano_activo(db, est, obrigatorio=False)
    turmas = db.scalars(select(Turma).where(Turma.estabelecimento_id == est,
                                            Turma.ano_id == (ano.id if ano else -1))).all()
    citadas = _turmas_citadas(pergunta, turmas)
    nomes = ", ".join(t.designacao for t in citadas)
    linhas = fin.cobrancas_detalhe(db, est)
    if citadas:
        ids = {t.id for t in citadas}
        linhas = [x for x in linhas if x["turma_id"] in ids]

    if any(k in q for k in ("mais atraso", "pior", "mais dívida", "mais divida")):
        por_turma = sorted(fin.resumo_por_turma(db, est), key=lambda g: -g["em_atraso"])
        if not por_turma or por_turma[0]["em_atraso"] == 0:
            return "Nenhuma turma tem pagamentos em atraso."
        g = por_turma[0]
        return f"A turma {g['turma']} tem mais atraso: {kz(g['em_atraso'])} (taxa de cobrança {g['taxa_cobranca']}%)."
    if any(k in q for k in ("refeit", "aliment", "almoço", "almoco")):
        alunos_alim = len({x["aluno_id"] for x in linhas if x["categoria"] == "alimentacao"})
        alunos_uso = len({x["aluno_id"] for x in linhas if x["categoria"] == "refeitorio"})
        return f"{alunos_alim} alunos pagam alimentação e {alunos_uso} pagam só a utilização do refeitório."
    if any(k in q for k in ("dívida", "divida", "atraso", "devem", "deve", "devedor")):
        dv = [x for x in linhas if x["dias_atraso"] > 0 and x["valor_em_aberto"] > 0]
        n = len({x["aluno_id"] for x in dv})
        return (f"{n} aluno(s) em dívida{' (' + nomes + ')' if nomes else ''}, "
                f"num total de {kz(sum(x['valor_em_aberto'] for x in dv))}.")
    if "taxa" in q or "cobrança" in q or "cobranca" in q:
        r = fin.resumo(linhas)
        return f"A taxa de cobrança{' de ' + nomes if nomes else ''} é {r['taxa_cobranca']}% ({kz(r['recebido'])} de {kz(r['facturado'])})."
    if "recebid" in q or "recebemos" in q or "entrou" in q:
        mes = primeiro_dia(date.today())
        v = db.scalar(select(func.coalesce(func.sum(Pagamento.valor), 0)).where(
            Pagamento.estabelecimento_id == est, Pagamento.estado == "confirmado", Pagamento.data_pagamento >= mes))
        return f"Este mês foram recebidos {kz(float(v))}."
    if "quantos alunos" in q or "número de alunos" in q or "numero de alunos" in q:
        stmt = select(func.count()).select_from(Matricula).where(Matricula.ano_id == (ano.id if ano else -1),
                                                                 Matricula.estado == "activa")
        if citadas:
            stmt = stmt.where(Matricula.turma_id.in_([t.id for t in citadas]))
        return f"{db.scalar(stmt)} aluno(s) matriculado(s){' em ' + nomes if nomes else ''}."
    return ("Ainda não sei responder a isso. Pergunte sobre dívidas, turmas com mais atraso, taxa de cobrança, "
            "valores recebidos, número de alunos ou refeitório.")


@router.post("/assistente", response_model=R.RespostaAssistente, summary="Perguntas em linguagem natural (só leitura)")
def assistente(dados: PerguntaIn, ctx: Contexto = Depends(exige("painel")), db: Session = Depends(get_db)):
    fonte, consulta = "regras", "regras:v1"
    resposta = None
    if assistente_ia.disponivel():
        try:
            resposta, chamadas = assistente_ia.responder(db, ctx.estabelecimento_id, dados.pergunta)
            fonte, consulta = "ia", json.dumps(chamadas, ensure_ascii=False, default=str)
        except anthropic.APIError:
            logging.getLogger("clacs.assistente").exception("A API da Claude falhou; a responder por regras")
    if resposta is None:
        resposta = responder(db, ctx.estabelecimento_id, dados.pergunta)
    db.add(IaConsultaLog(estabelecimento_id=ctx.estabelecimento_id, utilizador_id=ctx.id, pergunta=dados.pergunta,
                         consulta_gerada=consulta, resposta=resposta))
    db.commit()
    return {"resposta": resposta, "fonte": fonte}


# ---------------------------------------------------------------- relatórios e auditoria
@router.get("/relatorios/devedores", responses=R.EXCEL)
def relatorio_devedores(formato: str = "xlsx", ctx: Contexto = Depends(exige("propinas", "exportar")),
                        db: Session = Depends(get_db)):
    linhas = fin.devedores(db, ctx.estabelecimento_id)
    cols = [("aluno", "Aluno"), ("turma", "Turma"), ("encarregado", "Encarregado"), ("telefone", "Telefone"),
            ("cobrancas_em_atraso", "Cobranças em atraso"), ("total_em_divida", "Total em dívida (Kz)"),
            ("maior_atraso", "Maior atraso (dias)")]
    return tabela(cols, linhas, "devedores", formato)


@router.get("/relatorios/cobranca-turma", responses=R.EXCEL)
def relatorio_turmas(formato: str = "xlsx", ctx: Contexto = Depends(exige("propinas", "exportar")),
                     db: Session = Depends(get_db)):
    cols = [("turma", "Turma"), ("alunos", "Alunos"), ("facturado", "Facturado (Kz)"), ("recebido", "Recebido (Kz)"),
            ("em_atraso", "Em atraso (Kz)"), ("taxa_cobranca", "Taxa de cobrança (%)")]
    return tabela(cols, fin.resumo_por_turma(db, ctx.estabelecimento_id), "cobranca-por-turma", formato)


@router.get("/relatorios/agendados", response_model=list[R.RelatorioAgendado])
def relatorios_agendados(ctx: Contexto = Depends(exige("painel")), db: Session = Depends(get_db)):
    return [as_dict(r) for r in db.scalars(select(RelatorioAgendado).where(
        RelatorioAgendado.estabelecimento_id == ctx.estabelecimento_id))]


@router.post("/relatorios/agendados", response_model=R.RelatorioCriado, status_code=201, summary="Agenda um relatório (cron, ex.: '0 7 * * 1' = segundas às 7h)")
def agendar_relatorio(dados: RelatorioIn, ctx: Contexto = Depends(exige("painel", "criar")), db: Session = Depends(get_db)):
    r = RelatorioAgendado(estabelecimento_id=ctx.estabelecimento_id, **dados.model_dump())
    try:
        proxima = relatorios.proxima_execucao(r, datetime.now(timezone.utc))
    except ValueError as e:
        raise HTTPException(422, f"Agenda (cron) inválida: {e}")
    db.add(r)
    db.commit()
    return {**as_dict(r), "proxima_execucao": proxima}


@router.patch("/relatorios/agendados/{rid}", response_model=R.RelatorioAgendado, summary="Activa ou desactiva um relatório agendado")
def activar_relatorio(rid: int, activo: bool, ctx: Contexto = Depends(exige("painel", "editar")),
                      db: Session = Depends(get_db)):
    r = obter(db, RelatorioAgendado, rid, ctx.estabelecimento_id, "Relatório")
    r.activo = activo
    db.commit()
    return as_dict(r)


@router.post("/relatorios/agendados/{rid}/executar", response_model=R.RelatorioExecucao, summary="Gera e envia o relatório agora")
def executar_relatorio(rid: int, ctx: Contexto = Depends(exige("painel", "criar")), db: Session = Depends(get_db)):
    r = obter(db, RelatorioAgendado, rid, ctx.estabelecimento_id, "Relatório")
    try:
        return as_dict(relatorios.executar(db, r))
    except ErroIntegracao as e:
        raise HTTPException(502, str(e))


@router.get("/relatorios/agendados/{rid}/execucoes", response_model=list[R.RelatorioExecucao])
def execucoes(rid: int, ctx: Contexto = Depends(exige("painel")), db: Session = Depends(get_db)):
    obter(db, RelatorioAgendado, rid, ctx.estabelecimento_id, "Relatório")
    return [as_dict(e) for e in db.scalars(select(RelatorioExecucao).where(RelatorioExecucao.relatorio_id == rid)
                                           .order_by(RelatorioExecucao.id.desc()).limit(50))]


@router.get("/agendador", response_model=R.EstadoAgendador, summary="Estado do agendador interno e próximas execuções")
def estado_agendador(ctx: Contexto = Depends(exige("utilizadores"))):
    return agendador.estado()


@router.get("/auditoria", response_model=list[R.AuditoriaComUtilizador])
def auditoria(tabela_: str | None = None, limit: int = 200, ctx: Contexto = Depends(exige("utilizadores")),
              db: Session = Depends(get_db)):
    stmt = (select(AuditoriaLog, Utilizador.nome).outerjoin(Utilizador, Utilizador.id == AuditoriaLog.utilizador_id)
            .where(AuditoriaLog.estabelecimento_id == ctx.estabelecimento_id))
    if tabela_:
        stmt = stmt.where(AuditoriaLog.tabela == tabela_)
    return [{**as_dict(a), "utilizador": n} for a, n in db.execute(stmt.order_by(AuditoriaLog.id.desc()).limit(min(limit, 1000)))]


@router.get("/health", response_model=R.Ok, tags=["Sistema"])
def health(db: Session = Depends(get_db)):
    db.execute(select(1))
    return {"estado": "ok"}



# ---------------------------------------------------------------- avaliação dos professores pelos encarregados
@router.get("/professores/avaliacoes", response_model=list[R.ResumoAvaliacaoProfessor],
            summary="Como os encarregados avaliam cada professor: média, distribuição e comentários (anónimos)")
def avaliacoes_professores(periodo_id: int | None = None, ctx: Contexto = Depends(exige("painel")),
                           db: Session = Depends(get_db)):
    stmt = (select(AvaliacaoProfessor, Funcionario.nome_completo, PeriodoLectivo.designacao)
            .join(Funcionario, Funcionario.id == AvaliacaoProfessor.funcionario_id)
            .join(PeriodoLectivo, PeriodoLectivo.id == AvaliacaoProfessor.periodo_id)
            .where(AvaliacaoProfessor.estabelecimento_id == ctx.estabelecimento_id))
    if periodo_id:
        stmt = stmt.where(AvaliacaoProfessor.periodo_id == periodo_id)
    resumo: dict[int, dict] = {}
    for a, nome, periodo in db.execute(stmt.order_by(AvaliacaoProfessor.criado_em.desc())):
        r = resumo.setdefault(a.funcionario_id, {"funcionario_id": a.funcionario_id, "professor": nome, "notas": [],
                                                 "comentarios": []})
        r["notas"].append(a.nota)
        if (a.comentario or "").strip():
            r["comentarios"].append({"nota": a.nota, "comentario": a.comentario, "periodo": periodo,
                                     "criado_em": a.criado_em})
    out = []
    for r in resumo.values():
        notas = r.pop("notas")
        out.append({**r, "avaliacoes": len(notas), "media": round(sum(notas) / len(notas), 1),
                    "distribuicao": {str(n): notas.count(n) for n in range(1, 6)}})
    return sorted(out, key=lambda r: r["professor"])


# ---------------------------------------------------------------- CRUD completo (ver, apagar)
crud.registar(router, "/relatorios/agendados", RelatorioAgendado, nome="Relatório agendado", modulo="painel",
              resposta=R.RelatorioAgendado, ops=("ver", "apagar"))
