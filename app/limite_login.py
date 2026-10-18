"""Bloqueio temporário depois de várias tentativas de login falhadas.

Por e-mail: LOGIN_MAX_TENTATIVAS falhas (5). Por IP: 4 vezes mais, porque numa escola vários funcionários
saem para a internet pelo mesmo IP e um só não deve bloquear os outros.
Fica em memória: chega para uma instância da API. Com várias instâncias, passar para a base de dados ou Redis.
"""
import time
from collections import defaultdict, deque
from threading import Lock

from app.config import get_settings

_falhas: dict[str, deque] = defaultdict(deque)
_lock = Lock()


def _limite(chave: str) -> int:
    n = get_settings().login_max_tentativas
    return n * 4 if chave.startswith("ip:") else n


def bloqueado(*chaves: str) -> int:
    """Segundos até poder tentar outra vez (0 = pode tentar)."""
    janela, agora = get_settings().login_bloqueio_minutos * 60, time.monotonic()
    espera = 0
    with _lock:
        for c in chaves:
            q = _falhas[c]
            while q and agora - q[0] > janela:
                q.popleft()
            if len(q) >= _limite(c):
                espera = max(espera, int(janela - (agora - q[0])) + 1)
    return espera


def registar_falha(*chaves: str) -> None:
    with _lock:
        for c in chaves:
            _falhas[c].append(time.monotonic())


def limpar(*chaves: str) -> None:
    """Sem chaves, limpa tudo (testes)."""
    with _lock:
        if not chaves:
            _falhas.clear()
        for c in chaves:
            _falhas.pop(c, None)
