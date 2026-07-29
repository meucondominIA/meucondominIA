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

    # Storage dos anexos da ocorrência. A chave é SECRETA (ignora RLS); o bucket
    # é privado, então não há caminho anônimo de escrita nem de leitura.
    supabase_url: str
    supabase_secret_key: str
    anexos_bucket: str = "anexos"
    # Espelha o file_size_limit do bucket: recusar aqui evita subir para ouvir não.
    anexo_max_bytes: int = 5_242_880
    # MEDIDO 28/07/2026 (n=135, Supabase real): foto de WhatsApp p95 0,24s / max
    # 0,30s; 5 MB (teto) max 0,56s. Sem cauda — o Storage é PUT na mesma região,
    # não geração. 10s é 18x o pior caso e é o MESMO par do zpro_client, que já
    # está no caminho de espera do morador.
    storage_timeout_seconds: float = 10.0
    storage_connect_timeout_seconds: float = 5.0

    # Varredura de anexos órfãos. A carência precisa ser MAIOR que sessao_ttl_horas:
    # passado o TTL a conversa encerra e o rascunho some, então nenhum wizard vivo
    # pode estar segurando o arquivo. 2h sobre TTL de 1h = o dobro de folga, e a
    # checagem do rascunho na query já protege o wizard em andamento.
    anexo_orfao_horas: int = 2
    # Metade da carência: garante que um órfão seja varrido logo depois de
    # vencê-la, em vez de esperar o ciclo seguinte.
    faxina_interval_seconds: int = 1800
    faxina_batch_size: int = 100


settings = Settings()
