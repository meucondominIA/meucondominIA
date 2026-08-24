from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


# Contrato de configuração: declara e valida (com tipos) quais variáveis o app exige.
# Os valores/segredos vêm do ambiente (.env) — nunca ficam aqui.
class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str
    webhook_secret: str

    zpro_api_url: str
    zpro_api_token: str

    # Opcional porque só a CLI do QR lê: obrigatório derrubaria o webhook por
    # falta de config de uma ferramenta que nem roda no servidor.
    whatsapp_numero: str | None = None

    openai_api_key: str
    embedding_model: str = "text-embedding-3-large"
    embedding_dimensions: int = 3072
    rag_top_k: int = 5
    openai_timeout_busca_seconds: float = 3.0
    openai_retries_busca: int = 1
    openai_timeout_ingestao_seconds: float = 60.0
    openai_retries_ingestao: int = 2
    pgvector_schema: str = "extensions"

    openai_chat_model: str = "gpt-5.4-mini"
    openai_chat_effort: Literal[
        "none", "minimal", "low", "medium", "high", "xhigh", "max"
    ] = "none"
    openai_chat_verbosity: Literal["low", "medium", "high"] = "low"
    openai_timeout_chat_seconds: float = 6.0
    openai_retries_chat: int = 1

    pool_min_size: int = 1
    pool_max_size: int = 20

    sweeper_interval_seconds: int = 60
    sweeper_grace_seconds: int = 300
    sweeper_batch_size: int = 20

    # Espelha o "Resolver atendimento sem interação" do painel do Z-PRO: se o
    # nosso for mais lento, o morador lê "encerrado" e o bot segue como se nada.
    sessao_ttl_horas: int = 1
    encerrador_interval_seconds: int = 600

    reserva_janela_dias: int = 14

    # A lease tem que caber o lote: batch × latência < lease. Medido 29/07/2026,
    # ~1,1s por envio — 20 × 1,1s ≈ 22s contra 60s. Estourar duplica por rotina.
    aviso_interval_seconds: int = 30
    aviso_batch_size: int = 20
    aviso_lease_seconds: int = 60

    supabase_url: str
    supabase_secret_key: str  # chave SECRETA: ignora RLS, só no servidor
    anexos_bucket: str = "anexos"
    anexo_max_bytes: int = 5_242_880  # espelha o file_size_limit do bucket
    storage_timeout_seconds: float = 10.0
    storage_connect_timeout_seconds: float = 5.0

    # Precisa ser MAIOR que sessao_ttl_horas: só depois do TTL a conversa encerra
    # e o rascunho some, garantindo que nenhum wizard vivo segura o arquivo.
    anexo_orfao_horas: int = 2
    faxina_interval_seconds: int = 1800
    faxina_batch_size: int = 100


settings = Settings()
