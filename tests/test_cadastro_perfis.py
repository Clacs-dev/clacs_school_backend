"""Perfis e permissões (matriz editável pelo admin) e "O meu perfil" (nome, telefone, foto)."""
from app.database import SessionLocal
from scripts.seed import base
from tests.conftest import login

SENHA = "senha-de-teste-123"


def _ok(r, *codigos):
    assert r.status_code in (codigos or (200, 201, 204)), f"{r.request.method} {r.request.url.path}: {r.status_code} {r.text}"
    return r.json() if r.content and r.headers.get("content-type", "").startswith("application/json") else r


def test_matriz_de_permissoes(client, admin):
    cat = _ok(client.get("/api/permissoes/catalogo", headers=admin))
    assert {"transporte", "pedagogico"} <= {m["codigo"] for m in cat["modulos"]}
    perfis = {p["codigo"]: p for p in _ok(client.get("/api/perfis/permissoes", headers=admin))}
    assert not perfis["admin"]["editavel"]
    assert client.put(f"/api/perfis/{perfis['admin']['perfil_id']}/permissoes", headers=admin,
                      json={"permissoes": []}).status_code == 409

    prof = perfis["professor"]
    antes = prof["permissoes"]
    assert "pedagogico:ver" in antes
    try:
        # retirar "pedagogico:ver" ao professor fecha-lhe o pedagógico, mesmo depois de o seed voltar a correr
        sem = [x for x in antes if not x.startswith("pedagogico:")]
        r = _ok(client.put(f"/api/perfis/{prof['perfil_id']}/permissoes", headers=admin, json={"permissoes": sem}))
        assert "pedagogico:ver" not in r["permissoes"]
        with SessionLocal() as db:
            base(db)
            db.commit()
        assert "pedagogico:ver" not in next(p for p in client.get("/api/perfis/permissoes", headers=admin).json()
                                            if p["codigo"] == "professor")["permissoes"]
        h = login(client, "professor@clacs.ao", SENHA)
        assert client.get("/api/pedagogico/alunos", headers=h).status_code == 403
        assert client.put(f"/api/perfis/{prof['perfil_id']}/permissoes", headers=admin,
                          json={"permissoes": ["inventado:ver"]}).status_code == 422
    finally:
        _ok(client.put(f"/api/perfis/{prof['perfil_id']}/permissoes", headers=admin, json={"permissoes": antes}))
    assert client.get("/api/pedagogico/alunos", headers=login(client, "professor@clacs.ao", SENHA)).status_code == 200

    # quem não gere utilizadores não vê nem muda a matriz
    secretaria = login(client, "secretaria@teste.ao", "secretaria-123")
    assert client.get("/api/perfis/permissoes", headers=secretaria).status_code == 403


def test_o_meu_perfil(client):
    h = login(client, "professor@clacs.ao", SENHA)
    eu = _ok(client.get("/api/auth/me", headers=h))
    r = _ok(client.patch("/api/auth/me", headers=h, json={"nome": "Professora Teste", "telefone": "+244923111000"}))
    assert (r["nome"], r["telefone"]) == ("Professora Teste", "+244923111000")
    foto = _ok(client.post("/api/auth/me/foto", headers=h, files={"ficheiro": ("eu.png", b"\x89PNG\r\n\x1a\nx", "image/png")}))
    assert _ok(client.get("/api/auth/me", headers=h))["foto_url"] == foto["foto_url"]
    assert client.post("/api/auth/me/foto", headers=h, files={"ficheiro": ("x.pdf", b"%PDF", "application/pdf")}).status_code == 415
    _ok(client.patch("/api/auth/me", headers=h, json={"nome": eu["nome"]}))
