from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    database_url: str
    groq_api_key: str
    groq_model: str
    resolvelab_base_url: str = "http://localhost:8001"
    frontend_origin: str = "http://127.0.0.1:3000"
    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", extra="ignore")


settings = Settings()
