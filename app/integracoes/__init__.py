"""Ligações a serviços externos.

Cada adaptador fica em modo de simulação (só regista no log) até as credenciais estarem no .env,
para que o resto do sistema funcione igual em desenvolvimento, testes e produção.
"""


class ErroIntegracao(Exception):
    """Falha de um serviço externo; a mensagem é mostrada ao utilizador ou gravada no registo."""
