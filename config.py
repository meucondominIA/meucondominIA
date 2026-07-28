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

    # Espelha o "Resolver atendimento sem interação" do painel do Z-PRO, que manda
    # a despedida ao morador. Os dois números têm que andar juntos: se o nosso for
    # mais lento, o morador lê "atendimento encerrado" e o bot segue como se nada.
    sessao_ttl_horas: int = 1
    # Só a CADÊNCIA da varredura; o corte é o sessao_ttl_horas acima. Atraso aqui
    # não muda o que o morador vê — o comportamento sai de _sessao_expirada, que
    # compara o relógio na hora da mensagem. Isto só arruma a contabilidade.
    encerrador_interval_seconds: int = 600

    reserva_janela_dias: int = 14


settings = Settings()
