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

    pool_min_size: int = 1
    pool_max_size: int = 20

    sweeper_interval_seconds: int = 60
    sweeper_grace_seconds: int = 300
    sweeper_batch_size: int = 20


settings = Settings()
