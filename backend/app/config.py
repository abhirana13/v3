from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    redshift_host: str = ""
    redshift_port: int = 5439
    redshift_database: str = ""
    # Optional comma-separated allowlist of Redshift database names a chart may target
    # (all on the SAME cluster / credentials as redshift_database — only the db name
    # differs). Empty => only redshift_database is selectable.
    redshift_databases: str = ""
    redshift_user: str = ""
    redshift_password: str = ""

    # Optional SSH bastion / jump host for Redshift. When bastion_host is set, all
    # Redshift connections are opened through an SSH tunnel to (bastion_host:bastion_port)
    # authenticated with the private key at bastion_key_path, then forwarded to the
    # configured redshift_host:redshift_port. Empty bastion_host => direct connect.
    bastion_host: str = ""
    bastion_port: int = 22
    bastion_user: str = ""
    bastion_key_path: str = "/app/secrets/redshift_bastion.pem"

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

    def redshift_database_options(self) -> list[str]:
        """Databases a chart may target — the configured allowlist, always including the
        default (redshift_database) first. Empty allowlist => just the default. This is the
        set the per-chart selector offers and validates against."""
        names = [d.strip() for d in (self.redshift_databases or "").split(",") if d.strip()]
        if self.redshift_database and self.redshift_database not in names:
            names.insert(0, self.redshift_database)
        return names


settings = Settings()
