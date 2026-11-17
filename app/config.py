from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "http://localhost:3000"
    langfuse_enabled: bool = False
    use_mock_data: bool = True
    redis_url: str = "redis://localhost:6379/0"
    sqlite_path: str = "data/wine_after_sales.db"
    chroma_path: str = "data/chroma"
    app_env: str = "dev"
    log_level: str = "INFO"
    max_steps: int = 20
    max_retry: int = 2

settings = Settings()