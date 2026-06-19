from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    redshift_host: str = ""
    redshift_port: int = 5439
    redshift_database: str = ""
    redshift_user: str = ""
    redshift_password: str = ""

    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_db: str = "analytics_dash"
    postgres_user: str = "analytics"
    postgres_password: str = ""

    duckdb_path: str = "/data/aggregates.duckdb"

    log_level: str = "INFO"


settings = Settings()
