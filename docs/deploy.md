# Continuous deployment

Production (crm.letsrevel.io) runs from `~/infra/microcrm` on the personal server (repo `biagiodistefano/infra`),
using the published image `ghcr.io/biagiodistefano/microcrm:${MICROCRM_TAG}`.

Push to `main` → `Deploy` workflow:
1. **test** — reuses `test.yaml`.
2. **build-and-push** — pushes `:sha-<short>` and `:main` to GHCR (with the `GHCR_USERNAME`/`GHCR_TOKEN` secrets that
   `publish.yaml` also uses).
3. **deploy** — environment `production`, one at a time. SSHes to the server with a dedicated key whose
   `authorized_keys` entry forces `~/infra/microcrm/ci-deploy.sh`; the image tag arrives as `SSH_ORIGINAL_COMMAND`.

`ci-deploy.sh` (in the infra repo) accepts only `sha-<7–40 hex>`, takes a lock, writes a `pg_dump` to
`~/infra/microcrm/backups/` (last 14 kept), sets `MICROCRM_TAG` in `.env`, pulls, `up -d` (web runs migrations on
start), polls `/api/health` for up to 3 minutes and, if it never turns healthy, puts the previous tag back and fails.
The database is never restored automatically. Log: `~/infra/microcrm/deploy.log`.

Semver releases (`publish.yaml`) still push `:<version>` and `:latest`.

## Secrets (environment `production`)
`DEPLOY_SSH_KEY`, `DEPLOY_KNOWN_HOSTS`, `DEPLOY_HOST`, `DEPLOY_PORT`, `DEPLOY_USER`.

## Manual deploy / rollback
On the server: `~/infra/microcrm/ci-deploy.sh sha-<short>` (any tag that exists in GHCR), or re-run an older
successful Deploy run in GitHub Actions. Rolling back the image does not reverse migrations.

## Restore a database backup
```bash
cd ~/infra/microcrm && ls -lt backups | head
docker compose stop web celery_default beat
set -a; . ./.env; set +a
docker compose exec -T microcrm_postgres psql -U "$DB_USER" -d postgres \
  -c "DROP DATABASE \"$DB_NAME\" WITH (FORCE)" -c "CREATE DATABASE \"$DB_NAME\" OWNER \"$DB_USER\""
gunzip -c backups/<file>.sql.gz | docker compose exec -T microcrm_postgres psql -U "$DB_USER" -d "$DB_NAME"
./ci-deploy.sh sha-<the matching older build>
```
