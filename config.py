from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Obrigatórios (sem default) -> a app falha no startup se faltarem (fail-fast).
    # Mapeiam de DATABASE_URL / WEBHOOK_SECRET no ambiente (case-insensitive por padrão).
    database_url: str
    webhook_secret: str

    # Pool asyncpg (CLAUDE.md: regra workers × max_size <= ~40 na VPS KVM1).
    pool_min_size: int = 1
    pool_max_size: int = 20


settings = Settings()