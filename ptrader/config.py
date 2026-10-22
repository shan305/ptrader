from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, read from PTRADER_* environment variables or .env."""

    model_config = SettingsConfigDict(env_prefix="PTRADER_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./ptrader.db"
    # Apply pending migrations on startup. When off, startup fails unless the schema is current.
    auto_migrate: bool = True
    seed_on_startup: bool = True

    # Quotes kept per symbol; older ones are deleted on ingestion. 0 keeps everything.
    quote_retention_per_symbol: int = 10000

    # Background quote generator. When disabled, quotes only change through the API.
    simulator_enabled: bool = False
    simulator_interval_seconds: float = 1.0
    simulator_seed: int | None = None

    # How long shutdown waits for a worker's current run before cancelling it.
    worker_stop_timeout_seconds: float = 5.0
