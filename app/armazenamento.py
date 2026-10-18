"""Ficheiros enviados (documentos, fotos, comprovativos) e gerados (relatórios).

Guardados no disco, em UPLOAD_DIR, numa pasta por estabelecimento. No Render, o disco do serviço apaga-se a cada
deploy: use um "Persistent Disk" montado em UPLOAD_DIR, ou troque esta classe por um armazenamento S3/R2.
"""
import mimetypes
import uuid
from pathlib import Path

from fastapi import HTTPException

from app.config import get_settings

TIPOS_PERMITIDOS = {
    "application/pdf": ".pdf", "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx", "text/csv": ".csv",
}
CATEGORIAS = ("documentos", "fotos", "comprovativos", "relatorios")
# Primeiros bytes de cada formato: o tipo declarado pelo browser não chega.
ASSINATURAS = {".pdf": [b"%PDF"], ".jpg": [b"\xff\xd8\xff"], ".png": [b"\x89PNG"], ".webp": [b"RIFF"],
               ".xlsx": [b"PK"], ".csv": [b""]}


def raiz() -> Path:
    p = Path(get_settings().upload_dir).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def guardar(est: int, categoria: str, dados: bytes, mime: str | None, nome: str | None = None) -> str:
    """Grava o ficheiro e devolve a chave "<est>/<categoria>/<uuid><ext>"."""
    if categoria not in CATEGORIAS:
        raise ValueError(categoria)
    mime = (mime or mimetypes.guess_type(nome or "")[0] or "").split(";")[0].strip()
    ext = TIPOS_PERMITIDOS.get(mime)
    if not ext:
        raise HTTPException(415, "Tipo de ficheiro não aceite (use PDF, JPG, PNG ou WEBP)")
    if len(dados) > get_settings().upload_max_mb * 1024 * 1024:
        raise HTTPException(413, f"Ficheiro maior do que {get_settings().upload_max_mb} MB")
    if not any(dados.startswith(a) for a in ASSINATURAS[ext]):
        raise HTTPException(415, "O conteúdo do ficheiro não corresponde ao tipo indicado")
    chave = f"{est}/{categoria}/{uuid.uuid4().hex}{ext}"
    destino = raiz() / chave
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(dados)
    return chave


def url(chave: str) -> str:
    return f"/api/ficheiros/{chave}"


def caminho(chave: str, est: int) -> Path:
    """Caminho no disco de uma chave do estabelecimento; recusa chaves de outro estabelecimento ou fora da raiz."""
    partes = chave.split("/")
    if len(partes) != 3 or partes[0] != str(est) or partes[1] not in CATEGORIAS:
        raise HTTPException(404, "Ficheiro não encontrado")
    p = (raiz() / chave).resolve()
    if raiz() not in p.parents or not p.is_file():
        raise HTTPException(404, "Ficheiro não encontrado")
    return p
