"""Conteúdo dos PDFs. Quem chama já verificou as permissões e o estabelecimento."""
from datetime import date

from reportlab.lib.units import mm
from reportlab.platypus import Spacer
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import pdf
from app.models import (
    Aluno, AnoLectivo, Assiduidade, Avaliacao, Classe, Cobranca, Disciplina, Encarregado, Estabelecimento,
    Matricula, MetodoPagamento, Pagamento, PagamentoAlocacao, PeriodoLectivo, Servico, Turma,
)
from app.services.alertas import comportamento_actual
from app.services.financeiro import cobrancas_detalhe
from app.services.pedagogico import FALTAS, escala, media_ponderada

AVISO_FISCAL = "Documento interno. A factura legal é emitida pelo Primavera."


def recibo(db: Session, pg: Pagamento) -> bytes:
    est = db.get(Estabelecimento, pg.estabelecimento_id)
    ee = db.get(Encarregado, pg.encarregado_id) if pg.encarregado_id else None
    metodo = db.get(MetodoPagamento, pg.metodo_id)
    linhas = [["Aluno", "Serviço", "Mês", "Valor"]]
    for a, c, s, nome in db.execute(
            select(PagamentoAlocacao, Cobranca, Servico.nome, Aluno.nome_completo)
            .join(Cobranca, Cobranca.id == PagamentoAlocacao.cobranca_id)
            .join(Servico, Servico.id == Cobranca.servico_id).join(Aluno, Aluno.id == Cobranca.aluno_id)
            .where(PagamentoAlocacao.pagamento_id == pg.id)):
        linhas.append([pdf.esc(nome), pdf.esc(s), c.competencia.strftime("%m/%Y"), pdf.kz(a.valor)])
    linhas.append(["", "", "Total", pdf.kz(pg.valor)])
    estado = {"confirmado": "Confirmado", "pendente": "Pendente de confirmação", "rejeitado": "Rejeitado"}[pg.estado]
    blocos = [
        pdf.p(f"<b>Recibo n.º</b> {pg.id:06d} &nbsp;&nbsp; <b>Data:</b> {pdf.dt(pg.data_pagamento)} &nbsp;&nbsp; <b>Estado:</b> {estado}"),
        pdf.p(f"<b>Recebido de:</b> {pdf.esc(ee.nome_completo if ee else '—')}"
              f"{' &nbsp; NIF ' + pdf.esc(ee.nif) if ee and ee.nif else ''}"),
        pdf.p(f"<b>Método:</b> {pdf.esc(metodo.nome)}{' &nbsp; <b>Referência:</b> ' + pdf.esc(pg.referencia) if pg.referencia else ''}"),
        Spacer(1, 5 * mm), pdf.tabela(linhas, [70 * mm, 50 * mm, 22 * mm, 32 * mm], total=True),
        Spacer(1, 18 * mm), pdf.p("_______________________________<br/>A Tesouraria"),
    ]
    return pdf.gerar(est, "Recibo de pagamento", blocos, rodape=AVISO_FISCAL)


def extracto(db: Session, aluno: Aluno) -> bytes:
    est = db.get(Estabelecimento, aluno.estabelecimento_id)
    linhas_c = cobrancas_detalhe(db, aluno.estabelecimento_id, aluno_id=aluno.id)
    t = [["Mês", "Serviço", "Vencimento", "Valor", "Pago", "Em aberto", "Estado"]]
    nomes = {"paga": "Pago", "pendente": "Por pagar", "parcial": "Parcial"}
    for c in linhas_c:
        estado = nomes.get(c["estado"], c["estado"]) + (f" ({c['dias_atraso']} d)" if c["dias_atraso"] else "")
        t.append([c["competencia"].strftime("%m/%Y"), pdf.esc(c["servico"]), pdf.dt(c["vencimento"]), pdf.kz(c["valor_total"]),
                  pdf.kz(c["valor_pago"]), pdf.kz(c["valor_em_aberto"]), estado])
    t.append(["", "Total", "", pdf.kz(sum(c["valor_total"] for c in linhas_c)), pdf.kz(sum(c["valor_pago"] for c in linhas_c)),
              pdf.kz(sum(c["valor_em_aberto"] for c in linhas_c)), ""])
    turma = linhas_c[-1]["turma"] if linhas_c else "—"
    blocos = [pdf.p(f"<b>Aluno:</b> {pdf.esc(aluno.nome_completo)} &nbsp; <b>Processo:</b> {pdf.esc(aluno.numero_processo)}"
                    f" &nbsp; <b>Turma:</b> {pdf.esc(turma)} &nbsp; <b>Data:</b> {pdf.dt(date.today())}"),
              Spacer(1, 5 * mm),
              pdf.tabela(t, [18 * mm, 42 * mm, 22 * mm, 24 * mm, 24 * mm, 24 * mm, 22 * mm], total=True)]
    return pdf.gerar(est, "Extracto de conta corrente", blocos, rodape=AVISO_FISCAL)


def _matricula_activa(db: Session, aluno_id: int) -> tuple[Matricula, Turma, AnoLectivo] | None:
    return db.execute(select(Matricula, Turma, AnoLectivo).join(Turma, Turma.id == Matricula.turma_id)
                      .join(AnoLectivo, AnoLectivo.id == Matricula.ano_id)
                      .where(Matricula.aluno_id == aluno_id).order_by(AnoLectivo.data_inicio.desc())).first()


def boletim(db: Session, aluno: Aluno, periodo: PeriodoLectivo) -> bytes:
    est = db.get(Estabelecimento, aluno.estabelecimento_id)
    m = _matricula_activa(db, aluno.id)
    turma = m[1] if m else None
    discs = db.execute(select(Disciplina.id, Disciplina.nome).join(Avaliacao, Avaliacao.disciplina_id == Disciplina.id)
                       .where(Avaliacao.turma_id == (turma.id if turma else -1), Avaliacao.periodo_id == periodo.id)
                       .distinct().order_by(Disciplina.nome)).all()
    minima = escala(db, aluno.estabelecimento_id)["nota_minima"]
    t = [["Disciplina", "Média do período", "Situação"]]
    medias = []
    for did, nome in discs:
        v = media_ponderada(db, aluno.id, turma.id, did, periodo.id)
        if v is not None:
            medias.append(v)
        t.append([pdf.esc(nome), "—" if v is None else f"{v:.1f}",
                  "" if v is None else ("Positiva" if v >= minima else "Negativa")])
    geral = round(sum(medias) / len(medias), 1) if medias else None
    t.append(["Média geral", "—" if geral is None else f"{geral:.1f}", ""])
    faltas = db.scalar(select(func.count()).select_from(Assiduidade).where(
        Assiduidade.aluno_id == aluno.id, Assiduidade.estado.in_(FALTAS),
        Assiduidade.data >= periodo.data_inicio, Assiduidade.data <= periodo.data_fim))
    comp = {"bom": "Bom", "regular": "Regular", "atencao": "Requer atenção"}.get(comportamento_actual(db, aluno.id) or "", "—")
    blocos = [pdf.p(f"<b>Aluno:</b> {pdf.esc(aluno.nome_completo)} &nbsp; <b>Processo:</b> {pdf.esc(aluno.numero_processo)}"),
              pdf.p(f"<b>Turma:</b> {pdf.esc(turma.designacao if turma else '—')} &nbsp; <b>Ano lectivo:</b> "
                    f"{pdf.esc(m[2].designacao if m else '—')} &nbsp; <b>Período:</b> {pdf.esc(periodo.designacao)}"),
              Spacer(1, 5 * mm), pdf.tabela(t, [80 * mm, 40 * mm, 40 * mm], total=True), Spacer(1, 5 * mm),
              pdf.p(f"<b>Faltas no período:</b> {faltas} &nbsp;&nbsp; <b>Comportamento:</b> {comp}"),
              Spacer(1, 18 * mm), pdf.p("_______________________________<br/>O Director de Turma")]
    return pdf.gerar(est, "Boletim de avaliação", blocos)


def pauta(db: Session, turma: Turma, periodo: PeriodoLectivo) -> bytes:
    est = db.get(Estabelecimento, turma.estabelecimento_id)
    discs = db.execute(select(Disciplina.id, Disciplina.sigla, Disciplina.nome).join(Avaliacao, Avaliacao.disciplina_id == Disciplina.id)
                       .where(Avaliacao.turma_id == turma.id, Avaliacao.periodo_id == periodo.id)
                       .distinct().order_by(Disciplina.nome)).all()
    alunos = db.execute(select(Aluno.id, Aluno.numero_processo, Aluno.nome_completo).join(Matricula, Matricula.aluno_id == Aluno.id)
                        .where(Matricula.turma_id == turma.id, Matricula.estado == "activa").order_by(Aluno.nome_completo)).all()
    t = [["N.º", "Processo", "Aluno", *[pdf.esc(s or n[:4]) for _, s, n in discs], "Média"]]
    for i, (aid, proc, nome) in enumerate(alunos, start=1):
        ms = [media_ponderada(db, aid, turma.id, did, periodo.id) for did, _, _ in discs]
        val = [x for x in ms if x is not None]
        t.append([i, pdf.esc(proc), pdf.esc(nome), *["—" if x is None else f"{x:.1f}" for x in ms],
                  f"{sum(val) / len(val):.1f}" if val else "—"])
    ano = db.get(AnoLectivo, turma.ano_id)
    classe = db.get(Classe, turma.classe_id)
    blocos = [pdf.p(f"<b>Turma:</b> {pdf.esc(turma.designacao)} ({pdf.esc(classe.designacao)}) &nbsp; <b>Ano lectivo:</b> "
                    f"{pdf.esc(ano.designacao)} &nbsp; <b>Período:</b> {pdf.esc(periodo.designacao)}"),
              Spacer(1, 5 * mm), pdf.tabela(t), Spacer(1, 14 * mm),
              pdf.p("_______________________________ &nbsp;&nbsp;&nbsp;&nbsp; _______________________________<br/>"
                    "O Director de Turma &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; A Direcção Pedagógica")]
    return pdf.gerar(est, "Pauta de avaliação", blocos, horizontal=len(discs) > 5)


def declaracao(db: Session, m: Matricula) -> bytes:
    est = db.get(Estabelecimento, m.estabelecimento_id)
    a, t, ano = db.get(Aluno, m.aluno_id), db.get(Turma, m.turma_id), db.get(AnoLectivo, m.ano_id)
    classe = db.get(Classe, t.classe_id)
    nasc = f", nascido(a) a {pdf.dt(a.data_nascimento)}" if a.data_nascimento else ""
    texto = (f"Para os devidos efeitos, declara-se que <b>{pdf.esc(a.nome_completo)}</b>{nasc}, com o processo "
             f"n.º {pdf.esc(a.numero_processo)}, está matriculado(a) neste estabelecimento de ensino na "
             f"<b>{pdf.esc(classe.designacao)}</b>, turma <b>{pdf.esc(t.designacao)}</b>, no ano lectivo "
             f"<b>{pdf.esc(ano.designacao)}</b>.")
    if m.estado != "activa":
        texto += f" A matrícula encontra-se no estado: {pdf.esc(m.estado)}."
    blocos = [Spacer(1, 10 * mm), pdf.p(texto, pdf.CORPO), Spacer(1, 6 * mm),
              pdf.p("Por ser verdade e a pedido do(a) interessado(a), passa-se a presente declaração.", pdf.CORPO),
              Spacer(1, 10 * mm), pdf.p(f"Luanda, {pdf.dt(date.today())}"), Spacer(1, 20 * mm),
              pdf.p("_______________________________<br/>A Direcção")]
    return pdf.gerar(est, "Declaração de matrícula", blocos)
