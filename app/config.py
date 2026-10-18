import logging
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

SEGREDO_DEV = "dev-secret-local-mudar-em-producao-0000"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./clacs.db"
    jwt_secret: str = SEGREDO_DEV
    jwt_expira_minutos: int = 60 * 12
    admin_email: str = "admin@clacs.ao"
    admin_password: str = "admin123"
    cors_origins: str = "*"
    # Endereço público do frontend (links nos e-mails de recuperação de senha).
    frontend_url: str = "http://localhost:5173"

    # Segurança do login
    login_max_tentativas: int = 5
    login_bloqueio_minutos: int = 15

    # Aplica as migrações e cria os dados iniciais quando a API arranca (desligar com várias instâncias).
    auto_migrar: bool = True

    # Agendador interno (tarefas diárias e relatórios). Desligado nos testes.
    agendador_activo: bool = False
    agendador_hora: str = "06:00"
    fuso_horario: str = "Africa/Luanda"

    # Ficheiros enviados (documentos, fotos, comprovativos, relatórios gerados)
    upload_dir: str = "./uploads"
    upload_max_mb: int = 10

    # E-mail (SMTP). Sem SMTP_HOST, os e-mails só ficam no registo.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "CLACS Escola <no-reply@clacs.ao>"
    smtp_tls: bool = True

    # WhatsApp: "zapi" (Z-API) ou "meta" (Cloud API oficial). Sem credenciais do escolhido, só regista.
    whatsapp_provedor: str = "zapi"
    zapi_instance_id: str = ""
    zapi_token: str = ""
    zapi_client_token: str = ""     # "Security token" da conta Z-API (cabeçalho Client-Token)
    zapi_url: str = "https://api.z-api.io"

    # WhatsApp Cloud API (Meta).
    whatsapp_token: str = ""
    whatsapp_phone_id: str = ""
    whatsapp_api_versao: str = "v21.0"
    whatsapp_template: str = ""        # modelo aprovado para mensagens fora da janela de 24 h
    whatsapp_template_lingua: str = "pt_PT"

    # SMS (Twilio). Sem credenciais, só regista.
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from: str = ""

    # Pagamentos por referência (gateway). "simulado" até haver contrato.
    pagamentos_gateway: str = "simulado"
    pagamentos_webhook_segredo: str = ""
    pagamentos_entidade: str = "00000"

    # Assistente com IA (API da Claude). Sem chave, o assistente responde por regras fixas.
    anthropic_api_key: str = ""
    assistente_modelo: str = "claude-opus-5-5"

    # Notificações push (Firebase Cloud Messaging). Caminho do JSON da conta de serviço do Firebase.
    fcm_credenciais: str = ""

    @property
    def sqlalchemy_url(self) -> str:
        return normalizar_url(self.database_url)

    @property
    def em_producao(self) -> bool:
        return self.sqlalchemy_url.startswith("postgresql")


def normalizar_url(url: str) -> str:
    """O Render fornece "postgres://..."; o SQLAlchemy precisa do driver explícito."""
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg2://" + url[len("postgresql://"):]
    return url


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    if s.em_producao and (s.jwt_secret == SEGREDO_DEV or len(s.jwt_secret) < 32):
        raise RuntimeError("JWT_SECRET tem de ser definido (32+ caracteres) quando a base é PostgreSQL.")
    if s.em_producao and s.admin_password == "admin123":
        logging.getLogger("clacs").warning("ADMIN_PASSWORD ainda é a senha de desenvolvimento.")
    return s
