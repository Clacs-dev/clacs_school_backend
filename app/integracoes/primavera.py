"""Integração com o ERP Primavera (facturação legal e comunicação à AGT).

ESTADO: estrutura pronta, por validar com o cliente. Os caminhos da API abaixo seguem o padrão da Web API do
Primavera V10 (token OAuth + recursos por módulo), mas variam com a versão e a instalação. Antes de activar:
1. confirmar a versão e o endereço da Web API do Primavera do colégio;
2. confirmar os caminhos em CAMINHOS e o formato das respostas;
3. guardar as credenciais como variáveis de ambiente (nunca na base de dados) e pôr em
   integracao_config.credenciais_ref o prefixo delas, por exemplo "PRIMAVERA" → PRIMAVERA_USER, PRIMAVERA_PASSWORD,
   PRIMAVERA_COMPANY, PRIMAVERA_INSTANCE, PRIMAVERA_LINE.
Enquanto integracao_config.activo for falso, a sincronização corre em modo simulado.
"""
import hashlib
import json
import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integracoes import ErroIntegracao
from app.models import (
    Cobranca, DocumentoFiscal, Encarregado, IntegracaoConfig, IntegracaoDivergencia, IntegracaoMapeamento,
    IntegracaoSyncLog,
)

log = logging.getLogger("clacs.primavera")

# A VALIDAR com a instalação do cliente.
CAMINHOS = {
    "token": "/token",
    "cliente_editar": "/Base/Clientes/Actualiza",
    "documentos_venda": "/Vendas/Docs/LstDocumentos",
}


@dataclass
class DocumentoExterno:
    """Documento de venda lido do Primavera, já no formato da escola."""
    id_externo: str
    tipo: str            # fatura | fatura_recibo | recibo | nota_credito
    serie: str | None
    numero: str
    data_emissao: date
    nif_cliente: str | None
    total: Decimal
    estado_agt: str | None
    cobranca_id: int | None   # quando o documento traz a referência da cobrança da escola


class ClientePrimavera:
    def __init__(self, cfg: IntegracaoConfig):
        if not cfg.url_base or not cfg.credenciais_ref:
            raise ErroIntegracao("Configure url_base e credenciais_ref da integração Primavera.")
        p = cfg.credenciais_ref.upper()
        self.base = cfg.url_base.rstrip("/")
        self.cred = {k: os.environ.get(f"{p}_{k.upper()}", "") for k in ("user", "password", "company", "instance", "line")}
        if not self.cred["user"]:
            raise ErroIntegracao(f"Faltam as variáveis {p}_USER / {p}_PASSWORD no ambiente.")
        self._token: str | None = None

    def _auth(self) -> dict:
        if not self._token:
            dados = {"grant_type": "password", "username": self.cred["user"], "password": self.cred["password"],
                     "company": self.cred["company"], "instance": self.cred["instance"] or "DEFAULT",
                     "line": self.cred["line"] or "Professional"}
            r = self._pedido("POST", CAMINHOS["token"], data=dados, autenticado=False)
            self._token = r.get("access_token")
            if not self._token:
                raise ErroIntegracao("O Primavera não devolveu token.")
        return {"Authorization": f"Bearer {self._token}"}

    def _pedido(self, metodo: str, caminho: str, autenticado: bool = True, **kw):
        try:
            r = httpx.request(metodo, self.base + caminho, headers=self._auth() if autenticado else None,
                              timeout=30, **kw)
        except httpx.HTTPError as e:
            raise ErroIntegracao(f"Primavera indisponível: {e}") from e
        if r.status_code >= 400:
            raise ErroIntegracao(f"Primavera recusou {caminho} ({r.status_code}): {r.text[:300]}")
        return r.json() if r.content else {}

    def actualizar_cliente(self, e: Encarregado, codigo: str) -> str:
        self._pedido("POST", CAMINHOS["cliente_editar"], json={
            "Cliente": codigo, "Nome": e.nome_completo, "NumContribuinte": e.nif or "", "Telefone": e.telefone or "",
            "EnderecoWeb": e.email or "", "Morada": e.morada or ""})
        return codigo

    def documentos_desde(self, desde: date) -> list[DocumentoExterno]:
        bruto = self._pedido("GET", CAMINHOS["documentos_venda"], params={"dataInicio": desde.isoformat()})
        tipos = {"FT": "fatura", "FR": "fatura_recibo", "RE": "recibo", "NC": "nota_credito"}
        docs = []
        for d in bruto if isinstance(bruto, list) else bruto.get("Data", []):
            docs.append(DocumentoExterno(
                id_externo=str(d.get("Id") or d.get("ID")), tipo=tipos.get(d.get("TipoDoc"), "fatura"),
                serie=d.get("Serie"), numero=str(d.get("NumDoc")), data_emissao=date.fromisoformat(str(d.get("Data"))[:10]),
                nif_cliente=d.get("NumContribuinte"), total=Decimal(str(d.get("TotalDocumento", 0))),
                estado_agt=d.get("EstadoAGT"),
                cobranca_id=int(d["Referencia"]) if str(d.get("Referencia", "")).isdigit() else None))
        return docs


def _hash(dados: dict) -> str:
    return hashlib.sha256(json.dumps(dados, sort_keys=True, default=str).encode()).hexdigest()


def sincronizar(db: Session, est: int, cliente: ClientePrimavera | None = None, desde: date | None = None) -> IntegracaoSyncLog:
    """Envia clientes alterados e recebe documentos; regista divergências. Simulado se a integração estiver inactiva."""
    cfg = db.scalar(select(IntegracaoConfig).where(IntegracaoConfig.estabelecimento_id == est,
                                                   IntegracaoConfig.sistema == "primavera"))
    agora = datetime.now(timezone.utc)
    if cliente is None and (cfg is None or not cfg.activo):
        reg = IntegracaoSyncLog(estabelecimento_id=est, entidade="documento", direccao="recepcao", estado="ok",
                                total_registos=0, terminado_em=agora,
                                detalhe={"simulado": True, "nota": "Integração inactiva: nada foi trocado com o Primavera."})
        db.add(reg)
        db.commit()
        return reg
    cliente = cliente or ClientePrimavera(cfg)
    reg = IntegracaoSyncLog(estabelecimento_id=est, entidade="cliente+documento", direccao="recepcao", estado="em_curso")
    db.add(reg)
    db.flush()
    erros, total = [], 0

    # 1. Clientes (encarregados) novos ou alterados
    mapas = {m.id_local: m for m in db.scalars(select(IntegracaoMapeamento).where(
        IntegracaoMapeamento.estabelecimento_id == est, IntegracaoMapeamento.entidade == "cliente"))}
    for e in db.scalars(select(Encarregado).where(Encarregado.estabelecimento_id == est, Encarregado.activo)):
        h = _hash({"n": e.nome_completo, "nif": e.nif, "t": e.telefone, "e": e.email, "m": e.morada})
        m = mapas.get(e.id)
        if m and m.hash_dados == h:
            continue
        try:
            codigo = cliente.actualizar_cliente(e, m.id_externo if m else f"EE{e.id:06d}")
            if not m:
                m = IntegracaoMapeamento(estabelecimento_id=est, entidade="cliente", id_local=e.id, id_externo=codigo)
                db.add(m)
            m.hash_dados, m.ultima_sync = h, agora
            total += 1
        except ErroIntegracao as ex:
            erros.append({"encarregado": e.id, "erro": str(ex)})

    # 2. Documentos emitidos no Primavera
    try:
        docs = cliente.documentos_desde(desde or date(agora.year, 1, 1))
    except ErroIntegracao as ex:
        docs = []
        erros.append({"documentos": str(ex)})
    for d in docs:
        existente = db.scalar(select(DocumentoFiscal).where(DocumentoFiscal.estabelecimento_id == est,
                                                            DocumentoFiscal.id_primavera == d.id_externo))
        if existente:
            continue
        doc = DocumentoFiscal(estabelecimento_id=est, tipo=d.tipo, serie=d.serie, numero=d.numero,
                              data_emissao=d.data_emissao, nif_cliente=d.nif_cliente, total=d.total,
                              id_primavera=d.id_externo, estado_agt=d.estado_agt)
        db.add(doc)
        db.flush()
        total += 1
        c = db.get(Cobranca, d.cobranca_id) if d.cobranca_id else None
        if c and c.estabelecimento_id == est:
            c.documento_fiscal_id = doc.id
            if d.tipo in ("fatura_recibo", "recibo") and c.estado in ("pendente", "parcial"):
                db.add(IntegracaoDivergencia(estabelecimento_id=est, entidade="cobranca", id_local=c.id,
                                             id_externo=f"{d.serie or ''} {d.numero}".strip(), campo="estado",
                                             valor_local=c.estado, valor_externo="paga",
                                             descricao="Pago no Primavera, pendente na escola"))
        ee = db.get(Encarregado, c.encarregado_id) if c and c.encarregado_id else None
        if ee and ee.nif and d.nif_cliente and ee.nif != d.nif_cliente:
            db.add(IntegracaoDivergencia(estabelecimento_id=est, entidade="cliente", id_local=ee.id,
                                         id_externo=d.nif_cliente, campo="nif", valor_local=ee.nif,
                                         valor_externo=d.nif_cliente, descricao="NIF diferente entre os sistemas"))

    reg.total_registos, reg.total_erros = total, len(erros)
    reg.estado = "ok" if not erros else ("parcial" if total else "erro")
    reg.detalhe, reg.terminado_em = {"erros": erros[:50]}, datetime.now(timezone.utc)
    db.commit()
    return reg
