"""Envio de e-mail por SMTP (Gmail, Outlook, o servidor da escola…)."""
import logging
import smtplib
import uuid
from email.message import EmailMessage

from app.config import get_settings
from app.integracoes import ErroIntegracao

log = logging.getLogger("clacs.email")

# Nos testes e sem SMTP, os e-mails "enviados" ficam aqui para consulta.
CAIXA_LOCAL: list[EmailMessage] = []


def configurado() -> bool:
    return bool(get_settings().smtp_host)


def enviar_email(para: str | list[str], assunto: str, corpo: str,
                 anexos: list[tuple[str, bytes, str]] | None = None) -> str:
    """Envia um e-mail de texto. anexos = [(nome, conteúdo, tipo MIME)]. Devolve o Message-ID."""
    s = get_settings()
    msg = EmailMessage()
    msg["From"] = s.smtp_from
    msg["To"] = ", ".join(para) if isinstance(para, list) else para
    msg["Subject"] = assunto
    msg["Message-ID"] = f"<{uuid.uuid4()}@clacs>"
    msg.set_content(corpo)
    for nome, dados, mime in anexos or []:
        principal, _, sub = mime.partition("/")
        msg.add_attachment(dados, maintype=principal, subtype=sub or "octet-stream", filename=nome)

    if not configurado():
        log.info("[email simulado] para %s: %s", msg["To"], assunto)
        CAIXA_LOCAL.append(msg)
        return msg["Message-ID"]
    try:
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=20) as smtp:
            if s.smtp_tls:
                smtp.starttls()
            if s.smtp_user:
                smtp.login(s.smtp_user, s.smtp_password)
            smtp.send_message(msg)
    except (OSError, smtplib.SMTPException) as e:
        raise ErroIntegracao(f"Falha no envio de e-mail: {e}") from e
    return msg["Message-ID"]
