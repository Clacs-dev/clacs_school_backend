"""Conteúdo dos PDFs. Quem chama já verificou as permissões e o estabelecimento."""
from datetime import date

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Spacer, Table, TableStyle
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app import pdf
from app.models import (
    Aluno, AnoLectivo, Assiduidade, Avaliacao, Classe, Cobranca, Disciplina, Encarregado, Estabelecimento, FichaDisciplina, Funcionario,
    Horario, Matricula, MetodoPagamento, Pagamento, PagamentoAlocacao, PeriodoLectivo, ProgramaUnidade, Sala, Servico, Turma,
)
from app.programa_texto import numerar_conteudos  # noqa: F401  (reexportado para os testes)
from app.services.alertas import comportamento_actual
from app.services.calendario import assiduidade_professores
from app.services.financeiro import cobrancas_detalhe
from app.services.pedagogico import FALTAS, escala, media_ponderada, pauta_final

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
    return pdf.gerar(est, "Pauta de avaliação", blocos, horizontal=True)


def pauta_final_pdf(db: Session, turma: Turma) -> bytes:
    """Pauta final na horizontal: classificação final de cada disciplina, médias de cada trimestre e situação."""
    est = db.get(Estabelecimento, turma.estabelecimento_id)
    dados = pauta_final(db, turma)
    fmt = lambda v: "—" if v is None else f"{v:.1f}"  # noqa: E731
    cab = ["N.º", "Aluno", *[pdf.esc(d["sigla"] or d["nome"][:4]) for d in dados["disciplinas"]],
           *[f"Média {p.split(' ')[0]}T" for p in dados["periodos"]], "Média final", "Situação"]
    t = [cab]
    for i, ln in enumerate(dados["linhas"], start=1):
        t.append([i, pdf.esc(ln["aluno"]), *[fmt(d["final"]) for d in ln["disciplinas"]],
                  *[fmt(m) for m in ln["medias_trimestre"]], fmt(ln["media_final"]),
                  {"aprovado": "Aprovado", "reprovado": "Reprovado"}.get(ln["situacao"], "—")])
    ano, classe = db.get(AnoLectivo, turma.ano_id), db.get(Classe, turma.classe_id)
    legenda = " · ".join(f"{pdf.esc(d['sigla'] or d['nome'][:4])} = {pdf.esc(d['nome'])}" for d in dados["disciplinas"])
    blocos = [pdf.p(f"<b>Turma:</b> {pdf.esc(turma.designacao)} ({pdf.esc(classe.designacao)}) &nbsp; "
                    f"<b>Ano lectivo:</b> {pdf.esc(ano.designacao)} &nbsp; <b>Escala:</b> 0 a {dados['nota_maxima']:g}, "
                    f"positiva a partir de {dados['nota_minima']:g}"),
              Spacer(1, 4 * mm), pdf.tabela(t), Spacer(1, 3 * mm), pdf.p(f"<font size=7>{legenda}</font>"),
              Spacer(1, 12 * mm),
              pdf.p("_______________________________ &nbsp;&nbsp;&nbsp;&nbsp; _______________________________<br/>"
                    "O Director de Turma &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; A Direcção Pedagógica")]
    return pdf.gerar(est, "Pauta final", blocos, horizontal=True)


DIAS_SEMANA = ("Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado")
PERIODOS_DIA = {"manha": "Manhã", "tarde": "Tarde", "noite": "Noite"}


def _faixa(aulas, ini, fim) -> str:
    """Cabeça da linha do mapa de horário: a hora de início e o tempo (ex.: «07:30 · 1.º tempo»)."""
    tempo = next((h.tempo for h in aulas if (h.hora_inicio, h.hora_fim) == (ini, fim)), None)
    return f"{ini:%H:%M}<br/>{tempo}.º tempo" if tempo else f"{ini:%H:%M}<br/>às<br/>{fim:%H:%M}"


def horario_turma(db: Session, turma: Turma, dia: date) -> bytes:
    """Horário da turma em vigor nessa data, na horizontal (como o mapa de horário em papel): uma linha por intervalo
    de horas, uma coluna por dia; em cada aula a disciplina em maiúsculas, a sala quando não é a da turma e o nome
    completo do professor."""
    est = db.get(Estabelecimento, turma.estabelecimento_id)
    aulas = list(db.scalars(select(Horario).where(
        Horario.turma_id == turma.id, Horario.valido_desde <= dia, or_(Horario.valido_ate.is_(None), Horario.valido_ate >= dia))
        .order_by(Horario.hora_inicio, Horario.dia_semana, Horario.id)))
    dias = [d for d in range(1, 7) if d <= 5 or any(h.dia_semana == d for h in aulas)]
    faixas = sorted({(h.hora_inicio, h.hora_fim) for h in aulas})
    classe, ano = db.get(Classe, turma.classe_id), db.get(AnoLectivo, turma.ano_id)
    sala = db.get(Sala, turma.sala_id) if turma.sala_id else None
    periodo = db.scalar(select(PeriodoLectivo).where(PeriodoLectivo.ano_id == turma.ano_id, PeriodoLectivo.data_inicio <= dia,
                                                     PeriodoLectivo.data_fim >= dia))

    centro = ParagraphStyle("hc", parent=pdf.NORMAL, alignment=TA_CENTER, fontSize=9.5, leading=12)
    cab = ParagraphStyle("hcab", parent=centro, fontName="Helvetica-Bold", fontSize=11)
    hora = ParagraphStyle("hh", parent=centro, fontName="Helvetica-Bold", fontSize=10, leading=13)
    subtitulo = ParagraphStyle("hs", parent=centro, fontSize=12, leading=16)
    nota = ParagraphStyle("hn", parent=pdf.NORMAL, alignment=TA_RIGHT, fontSize=8.5)

    def aula(h: Horario) -> str:
        texto = f"<b>{pdf.esc(db.get(Disciplina, h.disciplina_id).nome.upper())}</b>"
        if h.sala_id and h.sala_id != turma.sala_id:
            texto += f"<br/><b><i>({pdf.esc(db.get(Sala, h.sala_id).designacao)})</i></b>"
        prof = db.get(Funcionario, h.funcionario_id) if h.funcionario_id else None
        return texto + f"<br/><br/><i>{pdf.esc(prof.nome_completo) if prof else 'Professor por atribuir'}</i>"

    linhas = [[pdf.p("Hora", cab), *[pdf.p(DIAS_SEMANA[d - 1], cab) for d in dias]]]
    for ini, fim in faixas:
        celulas = []
        for d in dias:
            daqui = [h for h in aulas if h.dia_semana == d and (h.hora_inicio, h.hora_fim) == (ini, fim)]
            celulas.append(pdf.p("<br/>——<br/>".join(aula(h) for h in daqui), centro) if daqui else "")
        linhas.append([pdf.p(_faixa(aulas, ini, fim), hora), *celulas])
    largura_hora = 24 * mm
    largura_dia = (261 * mm - largura_hora) / len(dias)
    t = Table(linhas, colWidths=[largura_hora, *[largura_dia] * len(dias)], repeatRows=1)
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.8, colors.black),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e6e6e6")), ("BACKGROUND", (0, 1), (0, -1), colors.HexColor("#e6e6e6")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 1), (-1, -1), 8), ("BOTTOMPADDING", (0, 1), (-1, -1), 8),
    ]))

    partes = [f"<b>{pdf.esc(classe.designacao)}</b>", f"<b><u>Turma {pdf.esc(turma.designacao)}</u></b>"]
    if sala:
        partes.append(f"<b><u>Sala {pdf.esc(sala.designacao)}</u></b>")
    partes.append(PERIODOS_DIA.get(turma.periodo_dia, turma.periodo_dia))
    rodape = " – ".join(x for x in [f"<i>Ano lectivo</i> {pdf.esc(ano.designacao)}",
                                    pdf.esc(periodo.designacao) if periodo else None, f"em vigor a {pdf.dt(dia)}"] if x)
    blocos = [pdf.p(" – ".join(partes), subtitulo), Spacer(1, 5 * mm),
              t if faixas else pdf.p("Esta turma ainda não tem horário nesta data.", centro),
              Spacer(1, 2 * mm), pdf.p(rodape, nota)]
    return pdf.gerar(est, f"Horário da turma {pdf.esc(turma.designacao)}", blocos, horizontal=True)


def horas_texto(minutos: int) -> str:
    """20 horas, 1 hora, 3h45."""
    h, m = divmod(minutos, 60)
    if m:
        return f"{h}h{m:02d}"
    return "1 hora" if h == 1 else f"{h} horas"


def horario_professor(db: Session, prof: Funcionario, dia: date) -> bytes:
    """Horário e carga horária semanal do professor em vigor nessa data (como o horário do docente em papel): linhas
    com os tempos da escola (os intervalos a cinzento), colunas de segunda a sábado; em cada aula a sigla da
    disciplina, a turma e a sala. Por cima, as unidades curriculares por extenso e a carga horária em horas."""
    # Só as turmas do ano lectivo dessa data (horários de anos anteriores podem ter ficado sem data de fim).
    ano = db.scalar(select(AnoLectivo).where(AnoLectivo.estabelecimento_id == prof.estabelecimento_id,
                                             AnoLectivo.data_inicio <= dia, AnoLectivo.data_fim >= dia)
                    .order_by(AnoLectivo.data_inicio.desc()).limit(1))
    escola = list(db.scalars(select(Horario).join(Turma, Turma.id == Horario.turma_id).where(
        Horario.estabelecimento_id == prof.estabelecimento_id, Horario.valido_desde <= dia,
        or_(Horario.valido_ate.is_(None), Horario.valido_ate >= dia), Turma.ano_id == (ano.id if ano else -1))))
    aulas = [h for h in escola if h.funcionario_id == prof.id]
    est = db.get(Estabelecimento, prof.estabelecimento_id)
    dias = [d for d in range(1, 7) if d <= 5 or any(h.dia_semana == d for h in escola)]
    faixas = sorted({(h.hora_inicio, h.hora_fim) for h in escola})
    discs = {h.disciplina_id: db.get(Disciplina, h.disciplina_id) for h in aulas}
    minutos = sum((h.hora_fim.hour * 60 + h.hora_fim.minute) - (h.hora_inicio.hour * 60 + h.hora_inicio.minute) for h in aulas)
    periodo = ano and db.scalar(select(PeriodoLectivo).where(PeriodoLectivo.ano_id == ano.id, PeriodoLectivo.data_inicio <= dia,
                                                            PeriodoLectivo.data_fim >= dia))

    centro = ParagraphStyle("pc", parent=pdf.NORMAL, alignment=TA_CENTER, fontSize=9.5, leading=12)
    cab = ParagraphStyle("pcab", parent=centro, fontName="Helvetica-Bold", fontSize=10.5)
    hora = ParagraphStyle("ph", parent=centro, fontName="Helvetica-Bold", fontSize=9.5, leading=12)
    linha = ParagraphStyle("pl", parent=pdf.NORMAL, fontSize=11, leading=16)
    direita = ParagraphStyle("pd", parent=pdf.NORMAL, alignment=TA_RIGHT, fontSize=10.5)
    nota = ParagraphStyle("pn", parent=pdf.NORMAL, alignment=TA_RIGHT, fontSize=8.5)

    def aula(h: Horario) -> str:
        d = discs[h.disciplina_id]
        texto = f"<font size=12><b>{pdf.esc((d.sigla or d.nome).upper())}</b></font><br/><b>{pdf.esc(db.get(Turma, h.turma_id).designacao)}</b>"
        if h.sala_id:
            texto += f"<br/><br/>(Sala {pdf.esc(db.get(Sala, h.sala_id).designacao)})"
        return texto

    linhas, intervalos = [[pdf.p("Hora", cab), *[pdf.p(DIAS_SEMANA[d - 1], cab) for d in dias]]], []
    anterior = None
    for ini, fim in faixas:
        if anterior and (ini.hour * 60 + ini.minute) - (anterior.hour * 60 + anterior.minute) >= 15:
            intervalos.append(len(linhas))                     # intervalo: linha a cinzento
            linhas.append([""] * (len(dias) + 1))
        anterior = fim
        celulas = []
        for d in dias:
            daqui = [h for h in aulas if h.dia_semana == d and (h.hora_inicio, h.hora_fim) == (ini, fim)]
            celulas.append(pdf.p("<br/>——<br/>".join(aula(h) for h in daqui), centro) if daqui else "")
        linhas.append([pdf.p(_faixa(escola, ini, fim), hora), *celulas])
    largura_hora = 24 * mm
    largura_dia = (261 * mm - largura_hora) / len(dias)
    alturas = [None] + [3 * mm if i in intervalos else None for i in range(1, len(linhas))]
    t = Table(linhas, colWidths=[largura_hora, *[largura_dia] * len(dias)], rowHeights=alturas, repeatRows=1)
    estilo = [
        ("GRID", (0, 0), (-1, -1), 0.8, colors.black),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#a6a6a6")), ("BACKGROUND", (0, 1), (0, -1), colors.HexColor("#d9d9d9")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 1), (-1, -1), 3), ("BOTTOMPADDING", (0, 1), (-1, -1), 4),
    ]
    for i in intervalos:
        estilo += [("BACKGROUND", (0, i), (-1, i), colors.HexColor("#a6a6a6")), ("TOPPADDING", (0, i), (-1, i), 0),
                   ("BOTTOMPADDING", (0, i), (-1, i), 0)]
    for i, ln in enumerate(linhas[1:], start=1):              # células com aula a cinzento claro, como no modelo
        for j, c in enumerate(ln[1:], start=1):
            if c:
                estilo.append(("BACKGROUND", (j, i), (j, i), colors.HexColor("#f2f2f2")))
    t.setStyle(TableStyle(estilo))

    unidades = "; ".join(f"{pdf.esc(d.nome)}" + (f" ({pdf.esc(d.sigla)})" if d.sigla else "")
                         for d in sorted(discs.values(), key=lambda d: d.nome))
    rodape = " – ".join(x for x in [f"Ano lectivo {pdf.esc(ano.designacao)}" if ano else None,
                                    pdf.esc(periodo.designacao) if periodo else None, f"em vigor a {pdf.dt(dia)}"] if x)
    blocos = [pdf.p(f"<i>Professor:</i> <font size=14><b>{pdf.esc(prof.nome_completo)}</b></font>", linha),
              pdf.p(f"<i>Unidade curricular:</i> <b><i>{unidades or '—'}</i></b>", linha), Spacer(1, 2 * mm),
              pdf.p(f"Carga horária: <b>{horas_texto(minutos)}</b> por semana ({len(aulas)} {'tempo' if len(aulas) == 1 else 'tempos'})", direita), Spacer(1, 3 * mm),
              t if faixas else pdf.p("A escola ainda não tem horários nesta data.", centro),
              Spacer(1, 2 * mm), pdf.p(rodape, nota)]
    return pdf.gerar(est, "Horário do professor", blocos, horizontal=True)


def _horas(minutos: int) -> str:
    """Horas com uma casa decimal só quando é preciso: 30, 22,5."""
    h = round(minutos / 60, 1)
    return f"{h:g}".replace(".", ",")


def assiduidade_professores_pdf(db: Session, est_id: int, desde: date, ate: date) -> bytes:
    """Mapa de assiduidade dos docentes no período (como o mapa em papel): horas previstas, faltas, horas leccionadas,
    por registar e atrasos, com os totais."""
    est = db.get(Estabelecimento, est_id)
    linhas = assiduidade_professores(db, est_id, desde, ate)
    cab = ParagraphStyle("acab", parent=pdf.NORMAL, fontName="Helvetica-Bold", fontSize=8.5, leading=10,
                         textColor=colors.white, alignment=TA_RIGHT)
    t = [[pdf.p("Professor", ParagraphStyle("acab0", parent=cab, alignment=0)),
          *[pdf.p(x, cab) for x in ("Previstas (hor.)", "Faltas (hor.)", "Horas leccio.", "Por registar (hor.)", "Atraso (min.)")]]]
    for x in linhas:
        t.append([pdf.esc(x["professor"]), _horas(x["minutos_previstos"]), _horas(x["minutos_faltas"]),
                  _horas(x["minutos_dados"]), _horas(x["minutos_por_registar"]), str(x["minutos_atraso"])])
    soma = lambda k: sum(x[k] for x in linhas)  # noqa: E731
    t.append(["Total", _horas(soma("minutos_previstos")), _horas(soma("minutos_faltas")), _horas(soma("minutos_dados")),
              _horas(soma("minutos_por_registar")), str(soma("minutos_atraso"))])
    tabela = pdf.tabela(t, [64 * mm, 22 * mm, 22 * mm, 22 * mm, 22 * mm, 22 * mm], total=True)
    tabela.setStyle(TableStyle([("ALIGN", (1, 0), (-1, -1), "RIGHT")]))
    blocos = [pdf.p(f"<b>Período lectivo</b> · Todos os docentes · {len(linhas)} professor(es)", pdf.NORMAL), Spacer(1, 4 * mm),
              tabela if linhas else pdf.p("Não há aulas previstas com professor neste período."), Spacer(1, 14 * mm),
              pdf.p("_______________________________<br/>A Direcção Pedagógica")]
    return pdf.gerar(est, f"Assiduidade entre {pdf.dt(desde)} e {pdf.dt(ate)}", blocos)


def tempos_semanais(teoricos: int | None, praticos: int | None) -> str:
    if teoricos is None and praticos is None:
        return "—"
    partes = [f"{teoricos} teórico{'s' if teoricos != 1 else ''}" if teoricos else None,
              f"{praticos} prático{'s' if praticos != 1 else ''}" if praticos else None]
    return " e ".join(x for x in partes if x) or "0"


def programa_disciplina(db: Session, classe: Classe, disciplina: Disciplina) -> bytes:
    """Programa da disciplina na classe, no formato do programa oficial: identificação, objectivos, programa
    (unidades e subunidades numeradas) e bibliografia."""
    est = db.get(Estabelecimento, disciplina.estabelecimento_id)
    ficha = db.scalar(select(FichaDisciplina).where(FichaDisciplina.classe_id == classe.id,
                                                    FichaDisciplina.disciplina_id == disciplina.id))
    unidades = list(db.scalars(select(ProgramaUnidade).where(ProgramaUnidade.classe_id == classe.id,
                                                             ProgramaUnidade.disciplina_id == disciplina.id)
                               .order_by(ProgramaUnidade.ordem, ProgramaUnidade.id)))
    titulo = ParagraphStyle("pt", parent=pdf.NORMAL, fontName="Helvetica-Bold", fontSize=11, spaceBefore=10, spaceAfter=6)
    item = ParagraphStyle("pi", parent=pdf.NORMAL, fontSize=10, leading=14, leftIndent=12, firstLineIndent=-12)
    unidade = ParagraphStyle("pu", parent=pdf.NORMAL, fontName="Helvetica-Bold", fontSize=10, leading=14, spaceBefore=6)
    nivel = {2: ParagraphStyle("p2", parent=pdf.NORMAL, fontSize=10, leading=14, leftIndent=14),
             3: ParagraphStyle("p3", parent=pdf.NORMAL, fontSize=10, leading=14, leftIndent=34),
             4: ParagraphStyle("p4", parent=pdf.NORMAL, fontSize=10, leading=14, leftIndent=58)}

    ident = Table([
        [pdf.p("<b>Designação da disciplina:</b>"), pdf.p(f"<b>{pdf.esc(disciplina.nome.upper())}</b>")],
        [pdf.p("<b>Regime:</b>"), pdf.p(pdf.esc(ficha.regime if ficha else "Anual"))],
        [pdf.p("<b>Classe:</b>"), pdf.p(pdf.esc(classe.designacao))],
        [pdf.p("<b>Tempos lectivos semanais:</b>"),
         pdf.p(pdf.esc(tempos_semanais(ficha.tempos_teoricos, ficha.tempos_praticos) if ficha else "—"))],
        [pdf.p("<b>Precedência obrigatória:</b>"), pdf.p(pdf.esc((ficha.precedencia if ficha else None) or "Não tem"))],
    ], colWidths=[55 * mm, 119 * mm])
    ident.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#9aa5a4")),
                               ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#eef2f1")), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    blocos = [ident, pdf.p("OBJECTIVOS", titulo)]
    objectivos = [x.strip().lstrip("•- ").strip() for x in ((ficha.objectivos if ficha else "") or "").splitlines() if x.strip()]
    blocos += [pdf.p(f"•&nbsp;&nbsp;{pdf.esc(x)}", item) for x in objectivos] or [pdf.p("—")]
    blocos.append(pdf.p("PROGRAMA", titulo))
    for n, u in enumerate(unidades, start=1):
        blocos.append(pdf.p(f"{n}.&nbsp;&nbsp;{pdf.esc(u.titulo.upper())}", unidade))
        blocos += [pdf.p(f"{num}&nbsp;&nbsp;{pdf.esc(texto)}", nivel[lv]) for lv, num, texto in numerar_conteudos(u.conteudos, n)]
    if not unidades:
        blocos.append(pdf.p("—"))
    blocos.append(pdf.p("BIBLIOGRAFIA", titulo))
    obras = [x.strip().lstrip("•- ").strip() for x in ((ficha.bibliografia if ficha else "") or "").splitlines() if x.strip()]
    blocos += [pdf.p(f"•&nbsp;&nbsp;{pdf.esc(x)}", item) for x in obras] or [pdf.p("—")]
    return pdf.gerar(est, f"Programa de {pdf.esc(disciplina.nome)} — {pdf.esc(classe.designacao)}", blocos)


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
