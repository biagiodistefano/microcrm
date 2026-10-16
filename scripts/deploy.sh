#!/usr/bin/env bash
# Deploy MicroCRM on the production server.
#
# Usage: scripts/deploy.sh [--build] <git-sha>
#
# Used by both the GitHub Actions deploy workflow (via a restricted SSH key whose
# authorized_keys entry forces this script, passing the sha in SSH_ORIGINAL_COMMAND)
# and prod_wizard.sh option 6, so manual and automatic deploys are the same path.
#
# Steps: record current image tag -> checkout sha -> pg_dump backup -> pull images
# -> up -d -> wait for /api/health -> on failure roll back the image tag.
# Database restores are never automatic.
set -euo pipefail

# Re-exec from a temp copy: the git checkout below may rewrite this very file while bash is reading it.
if [ -z "${DEPLOY_REEXEC:-}" ]; then
    tmp="$(mktemp)"
    cp "$0" "$tmp"
    DEPLOY_REEXEC=1 exec bash "$tmp" "$@"
fi
trap 'rm -f "$0"' EXIT

APP_DIR="${APP_DIR:-/opt/microcrm}"
IMAGE_REPO="ghcr.io/biagiodistefano/microcrm"
BACKUP_KEEP="${BACKUP_KEEP:-14}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-60}"
COMPOSE_FILE="compose.prod.yaml"

cd "$APP_DIR"
mkdir -p backups
chmod 700 backups
umask 077
LOG_FILE="$APP_DIR/deploy.log"
exec > >(tee -a "$LOG_FILE") 2>&1

log() { printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

BUILD_LOCAL=0
SHA=""
# When invoked through a forced SSH command, the argument comes via SSH_ORIGINAL_COMMAND.
if [ "$#" -eq 0 ] && [ -n "${SSH_ORIGINAL_COMMAND:-}" ]; then
    # shellcheck disable=SC2086
    set -- $SSH_ORIGINAL_COMMAND
fi
for arg in "$@"; do
    case "$arg" in
        --build) BUILD_LOCAL=1 ;;
        *) SHA="$arg" ;;
    esac
done

if ! [[ "$SHA" =~ ^[0-9a-f]{40}$ ]]; then
    echo "Usage: $0 [--build] <40-char git sha>" >&2
    exit 2
fi
SHORT="${SHA:0:7}"
NEW_TAG="sha-${SHORT}"

# Serialize deploys (manual vs automatic).
exec 9>"$APP_DIR/.deploy.lock"
if ! flock -n 9; then
    log "Another deploy is running; aborting."
    exit 3
fi

compose() {
    if [ "$BUILD_LOCAL" -eq 1 ]; then
        docker compose -f "$COMPOSE_FILE" -f compose.build.yaml "$@"
    else
        docker compose -f "$COMPOSE_FILE" "$@"
    fi
}

# MICROCRM_IMAGE_TAG lives in .env (compose's default env file), not the .env.prod secrets file.
read_tag() { grep -E '^MICROCRM_IMAGE_TAG=' .env 2>/dev/null | tail -n1 | cut -d= -f2- || true; }
write_tag() {
    local tag="$1" rest
    rest="$(grep -vE '^MICROCRM_IMAGE_TAG=' .env 2>/dev/null || true)"
    # Rewrite in place (keeps .env if it is a symlink).
    { [ -n "$rest" ] && printf '%s\n' "$rest"; printf 'MICROCRM_IMAGE_TAG=%s\n' "$tag"; } > .env
}

wait_healthy() {
    local deadline=$((SECONDS + HEALTH_TIMEOUT))
    while [ "$SECONDS" -lt "$deadline" ]; do
        # Run inside the web container; Host must match ALLOWED_HOSTS (DOMAIN).
        if compose exec -T web python -c '
import os, sys, urllib.request
req = urllib.request.Request("http://127.0.0.1:8000/api/health",
    headers={"Host": os.environ.get("DOMAIN") or "localhost", "X-Forwarded-Proto": "https"})
sys.exit(0 if urllib.request.urlopen(req, timeout=5).status == 200 else 1)
' >/dev/null 2>&1; then
            return 0
        fi
        sleep 3
    done
    return 1
}

PREV_TAG="$(read_tag)"
PREV_SHA="$(git rev-parse HEAD 2>/dev/null || true)"
log "Deploy start: sha=$SHA tag=$NEW_TAG previous_tag=${PREV_TAG:-<none>} previous_sha=${PREV_SHA:-<none>}"

# Check out the exact commit so compose/Caddyfile changes deploy together with the image.
git fetch --quiet origin
git checkout --quiet --detach "$SHA"

# Backup before migrating (migrations run when the web container starts).
BACKUP="$APP_DIR/backups/$(date -u +%Y%m%dT%H%M%SZ)-${SHORT}.sql.gz"
if compose ps --status running --services 2>/dev/null | grep -qx postgres; then
    DB_USER_NAME="$(compose exec -T postgres printenv POSTGRES_USER | tr -d '\r')"
    DB_NAME_NAME="$(compose exec -T postgres printenv POSTGRES_DB | tr -d '\r')"
    compose exec -T postgres pg_dump -U "$DB_USER_NAME" "$DB_NAME_NAME" | gzip > "$BACKUP"
    if [ ! -s "$BACKUP" ]; then
        log "Backup is empty; aborting before touching anything."
        rm -f "$BACKUP"
        exit 1
    fi
    log "Backup written: $BACKUP"
else
    log "Postgres not running (first deploy?); skipping backup."
    BACKUP=""
fi
# Keep the newest $BACKUP_KEEP backups.
# shellcheck disable=SC2012
ls -1t backups/*.sql.gz 2>/dev/null | tail -n +"$((BACKUP_KEEP + 1))" | xargs -r rm -f --

write_tag "$NEW_TAG"
export MICROCRM_IMAGE_TAG="$NEW_TAG"

if [ "$BUILD_LOCAL" -eq 1 ]; then
    log "Building image locally as $IMAGE_REPO:$NEW_TAG"
    compose build
else
    log "Pulling images"
    compose pull
fi
compose up -d --remove-orphans

if wait_healthy; then
    log "Deploy OK: $NEW_TAG is healthy."
    docker image prune -f >/dev/null 2>&1 || true
    exit 0
fi

log "HEALTH CHECK FAILED for $NEW_TAG after ${HEALTH_TIMEOUT}s."
compose logs --tail=50 web || true
if [ -n "$PREV_TAG" ] && [ "$PREV_TAG" != "$NEW_TAG" ]; then
    log "Rolling back image to $PREV_TAG (code to ${PREV_SHA:0:7})"
    write_tag "$PREV_TAG"
    export MICROCRM_IMAGE_TAG="$PREV_TAG"
    [ -n "$PREV_SHA" ] && git checkout --quiet --detach "$PREV_SHA"
    compose up -d --remove-orphans || true
    if wait_healthy; then log "Rollback healthy on $PREV_TAG."; else log "Rollback is NOT healthy either; manual intervention needed."; fi
else
    log "No previous tag recorded; cannot roll back automatically."
fi
if [ -n "$BACKUP" ]; then
    log "The database was NOT restored automatically. If the new migrations changed data/schema, restore with:"
    log "  gunzip -c $BACKUP | docker compose -f $COMPOSE_FILE exec -T postgres psql -U <db_user> <db_name>   (see docs/deploy.md)"
fi
exit 1
