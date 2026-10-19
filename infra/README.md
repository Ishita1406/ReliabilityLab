# infra/

Local development infrastructure.

## PostgreSQL (`docker-compose.yml`)

A project-scoped PostgreSQL 18.6 container. It is bound to `127.0.0.1:5433` (not 5432, to
avoid clashing with a system PostgreSQL) and keeps data in the named volume
`reliabilitylab_reliabilitylab-pgdata`.

```bash
docker compose -f infra/docker-compose.yml up -d postgres     # start (waits for healthcheck)
docker compose -f infra/docker-compose.yml ps                 # status
docker compose -f infra/docker-compose.yml stop postgres      # stop, keep data
docker compose -f infra/docker-compose.yml down -v            # remove container AND data volume
```

The credentials in the compose file (`reliabilitylab` / `reliabilitylab_local_dev`) are **local-only
defaults**. Override them with `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, and
`POSTGRES_PORT` in your environment; never reuse them anywhere real.

No other services (Redis, workers, deployment manifests) exist yet.
