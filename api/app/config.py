from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://acme:acme_dev_password@postgres:5432/acme"
    redis_url: str = "redis://redis:6379/0"
    keycloak_url: str = "http://keycloak:8080"
    keycloak_realm: str = "master"  # 'acme' from Phase 2
    mcp_server_url: str = "http://mcp-server:8765/mcp"
    openai_api_key: str = ""
    primary_model: str = "gpt-5.6-sol"
    fallback_models: str = "gpt-5.5,gpt-5.4"
    phoenix_collector_endpoint: str = "http://phoenix:6006"


settings = Settings()
