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

    # Per-connection caps for the per-chart caches. DuckDB defaults memory_limit to ~80% of
    # host RAM (measured 6.1 GiB here) and threads to the core count (10) — PER DATABASE
    # INSTANCE. With one shared cache file every connection in a process shared a single
    # instance and so a single budget; now that each chart is its own file, N charts touched
    # at once are N instances that each independently believe they may use 6.1 GiB and spawn
    # 10 threads, with nothing capping the total. On an 8 GB box also running Postgres, two
    # uvicorn workers and the backpop worker, that is an OOM waiting for a busy moment.
    #
    # The caches are tiny — the largest is 2.6 MB — so 256 MB is ~100x the biggest file and
    # will not spill; 2 threads is plenty for scans of that size and stops N instances
    # oversubscribing the CPU. Env-tunable so prod can differ without a code change.
    duckdb_memory_limit: str = "256MB"
    duckdb_threads: int = 2

    # Trailing window (in days, ending today) that backpopulation ALWAYS re-pulls and
    # overwrites in daily+append mode, so late-arriving / restated recent data is picked
    # up without a query change. Older days keep the cheap fill-missing (skip) behavior.
    #
    # 5, not 4, because of look-AHEAD queries. The cutoff is today-(window-1) and the nightly
    # only processes up to yesterday, so day D is last re-pulled on the night T = D+window-1.
    # A query that reads D+N (rolling retention reads D+1 and D+3) needs D+N <= T-1 at that
    # moment, i.e. window >= N+2. At window=4 the last re-pull of D happens on T = D+3, when
    # D+3 IS today and only ~3 hours old at 03:00 UTC — so d3_returned was measured against a
    # nearly empty day and never revisited. Measured on 2026-08-01: 1,345 of the true 4,698
    # D3 returners, i.e. 13.5% reported against an actual 47.3%. d1_returned was unaffected.
    #
    # Cost of the extra day: one more day re-pulled per chart per night. A D7 column would
    # need >= 9. Also requires each chart's default_backpop_days >= this value, or the day
    # falls outside the nightly's range before its window closes (default is 7).
    backpop_refresh_window_days: int = 5

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
