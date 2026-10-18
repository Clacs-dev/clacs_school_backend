"""Documentos PDF: recibo, extracto, boletim, pauta e declaração de matrícula (reportlab)."""
import io
from datetime import date
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_JUSTIFY
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.models import Estabelecimento

_est = getSampleStyleSheet()
TITULO = ParagraphStyle("titulo", parent=_est["Title"], fontSize=16, spaceAfter=4)
NORMAL = ParagraphStyle("normal", parent=_est["Normal"], fontSize=10, leading=14)
CORPO = ParagraphStyle("corpo", parent=NORMAL, fontSize=11, leading=17, alignment=TA_JUSTIFY)
PEQUENO = ParagraphStyle("pequeno", parent=NORMAL, fontSize=8, textColor=colors.grey)
TEAL = colors.HexColor("#0f5c63")


def kz(v) -> str:
    return f"{float(v or 0):,.2f}".replace(",", " ").replace(".", ",") + " Kz"


def dt(d: date | None) -> str:
    return d.strftime("%d/%m/%Y") if d else "—"


def tabela(linhas: list[list], larguras: list[float] | None = None, total: bool = False) -> Table:
    t = Table(linhas, colWidths=larguras, repeatRows=1)
    estilo = [
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9), ("FONT", (0, 1), (-1, -1), "Helvetica", 9),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("BACKGROUND", (0, 0), (-1, 0), TEAL),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f1f5f4")]),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#c9d4d2")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    if total:
        estilo += [("FONT", (0, -1), (-1, -1), "Helvetica-Bold", 9), ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#e3f0ef"))]
    t.setStyle(TableStyle(estilo))
    return t


def gerar(est: Estabelecimento, titulo: str, blocos: list, rodape: str = "", horizontal: bool = False) -> bytes:
    buf = io.BytesIO()
    tamanho = landscape(A4) if horizontal else A4

    def cabecalho(canvas, doc):
        canvas.saveState()
        w, h = tamanho
        canvas.setFillColor(TEAL)
        canvas.setFont("Helvetica-Bold", 12)
        canvas.drawString(18 * mm, h - 14 * mm, est.nome)
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.grey)
        dados = " · ".join(x for x in [est.morada, f"NIF {est.nif}" if est.nif else None, est.telefone, est.email] if x)
        canvas.drawString(18 * mm, h - 19 * mm, dados)
        canvas.line(18 * mm, h - 22 * mm, w - 18 * mm, h - 22 * mm)
        canvas.drawString(18 * mm, 10 * mm, rodape or f"Gerado pelo CLACS Escola em {dt(date.today())}")
        canvas.drawRightString(w - 18 * mm, 10 * mm, f"Página {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(buf, pagesize=tamanho, topMargin=30 * mm, bottomMargin=18 * mm,
                            leftMargin=18 * mm, rightMargin=18 * mm, title=titulo, author=est.nome)
    doc.build([Paragraph(titulo, TITULO), Spacer(1, 4 * mm), *blocos], onFirstPage=cabecalho, onLaterPages=cabecalho)
    return buf.getvalue()


def p(texto: str, estilo: ParagraphStyle = NORMAL) -> Paragraph:
    """Parágrafo com marcação reportlab (<b>…</b>); dados vindos da base passam por esc()."""
    return Paragraph(texto, estilo)


def esc(v) -> str:
    return escape(str(v if v is not None else "—"))
