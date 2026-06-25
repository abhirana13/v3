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

    # Trailing window (in days, ending today) that backpopulation ALWAYS re-pulls and
    # overwrites in daily+append mode, so late-arriving / restated recent data is picked
    # up without a query change. Older days keep the cheap fill-missing (skip) behavior.
    backpop_refresh_window_days: int = 4

    log_level: str = "INFO"


settings = Settings()
