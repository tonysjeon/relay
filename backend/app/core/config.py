from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://relay:relay@localhost:5432/relay"
    redis_url: str = "redis://localhost:6379/0"
    retry_scan_interval_seconds: float = Field(default=2, gt=0, allow_inf_nan=False)
    worker_heartbeat_seconds: float = Field(default=10, gt=0, allow_inf_nan=False)
    worker_timeout_seconds: float = Field(default=30, gt=0, allow_inf_nan=False)
    step_lease_seconds: float = Field(default=30, gt=0, allow_inf_nan=False)
    lease_scan_interval_seconds: float = Field(default=5, gt=0, allow_inf_nan=False)
    ready_scan_interval_seconds: float = Field(default=5, gt=0, allow_inf_nan=False)
    openai_api_key: SecretStr | None = None
    openai_model: str = Field(default="gpt-4.1-mini", min_length=1)
    openai_timeout_seconds: float = Field(default=60, gt=0, allow_inf_nan=False)
