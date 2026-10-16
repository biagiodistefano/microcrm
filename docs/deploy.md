# Continuous deployment

Push to `main` -> `Deploy` workflow: **test** (reuses `test.yaml`) -> **build-and-push** (GHCR image
`ghcr.io/biagiodistefano/microcrm:sha-<short>` and `:main`) -> **deploy** (SSH to the server, run
`scripts/deploy.sh <sha>`). Semver releases (`publish.yaml`) are unchanged and also tag `:latest`.

`scripts/deploy.sh` (on the server, also behind `./prod_wizard.sh` option 6):
checks out the exact commit (so `compose.prod.yaml` / `Caddyfile` changes deploy too), writes a
`pg_dump` to `/opt/microcrm/backups/<timestamp>-<sha>.sql.gz` (last 14 kept), pulls the image,
`up -d --remove-orphans` (web runs migrations on start), polls `/api/health` for 60 s, and rolls the
image tag back if it never turns healthy. The database is never restored automatically. Log:
`/opt/microcrm/deploy.log`. The current tag is stored as `MICROCRM_IMAGE_TAG` in `/opt/microcrm/.env`
(compose's default env file; secrets stay in `.env.prod`).

## One-time setup (in order)

Scratchpad paths below are examples; use any private directory and delete the key files after step 4.

### 1. Generate a dedicated deploy key (locally)

```bash
KEYDIR=$(mktemp -d) && chmod 700 "$KEYDIR"
ssh-keygen -t ed25519 -N "" -C "microcrm-deploy-gha" -f "$KEYDIR/deploy_key"
echo "$KEYDIR"   # remember this path; do NOT cat deploy_key
```

### 2. Create the deploy user on the server (you ssh in manually)

Use the droplet IP or a DNS-only hostname for SSH: Cloudflare-proxied hostnames do not proxy port 22.

```bash
sudo adduser --disabled-password --gecos "" deploy
sudo usermod -aG docker deploy      # note: docker group is root-equivalent; the key below is locked down
sudo chown -R deploy:deploy /opt/microcrm
sudo -u deploy git config --global --add safe.directory /opt/microcrm
sudo install -d -m 700 -o deploy -g deploy /home/deploy/.ssh
```

Add one line to `/home/deploy/.ssh/authorized_keys` (mode 600, owner deploy), replacing the key with the
contents of `$KEYDIR/deploy_key.pub`:

```
command="/opt/microcrm/scripts/deploy.sh",no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-pty ssh-ed25519 AAAA... microcrm-deploy-gha
```

The key can only run `deploy.sh`; the sha sent by CI arrives in `SSH_ORIGINAL_COMMAND` and the script
rejects anything that is not a 40-char hex sha. `git` on the server needs read access to the repo
(already true if `/opt/microcrm` was cloned over https from the public repo, or use a read-only deploy key).

Also make sure `scripts/deploy.sh` exists on the server (it arrives with the first `git pull` of this branch/PR once merged),
and `flock` is available (util-linux, default on Ubuntu).

### 3. GHCR access on the server

The Dockerfile's runtime stage only copies `.venv`, `src/`, `staticfiles/` and `entrypoint.sh`; no `.env*` file
is baked in (`.env.prod` is injected at runtime), so **making the package public is safe** and is the simplest option:
GitHub -> Packages -> `microcrm` -> Package settings -> Change visibility -> Public.

If you keep it private, log in once as the deploy user with a classic PAT that has only `read:packages`:

```bash
sudo -u deploy sh -c 'docker login ghcr.io -u biagiodistefano --password-stdin' < /path/to/pat_file
```

Also, because `publish.yaml` pushed this package with a PAT, open Package settings -> "Manage Actions access"
and add the `microcrm` repository with **Write** role, otherwise the workflow's `GITHUB_TOKEN` gets 403 on push.

### 4. Pin the host key and set GitHub secrets (from your machine)

```bash
HOST=<droplet-ip-or-dns-only-hostname>
ssh-keyscan -t ed25519 "$HOST" > "$KEYDIR/known_hosts"
ssh-keygen -lf "$KEYDIR/known_hosts"   # compare fingerprint with the server's: ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub

gh api -X PUT repos/biagiodistefano/microcrm/environments/production   # creates the environment (or use Settings -> Environments)
# Optionally add required reviewers there for a manual approval gate.

gh secret set DEPLOY_SSH_KEY     --env production < "$KEYDIR/deploy_key"
gh secret set DEPLOY_KNOWN_HOSTS --env production < "$KEYDIR/known_hosts"
printf '%s' "$HOST"   | gh secret set DEPLOY_HOST --env production
printf '%s' "deploy"  | gh secret set DEPLOY_USER --env production
shred -u "$KEYDIR/deploy_key" 2>/dev/null || rm -P "$KEYDIR/deploy_key"   # key now lives only in GitHub + authorized_keys
```

### 5. First manual run

After the PR is merged and CI has built an image for the merge commit:

```bash
ssh deploy-capable-user@server
cd /opt/microcrm && git fetch origin main
./scripts/deploy.sh "$(git rev-parse origin/main)"      # or: ./prod_wizard.sh -> 6
tail -f deploy.log
```

If no image exists yet for that sha (CI not finished), pass `--build` to build locally instead of pulling.
Then push any commit to `main` (or run the Deploy workflow via "Run workflow") to verify the full path.

## Rollback

Automatic: if `/api/health` is not healthy within 60 s, the script restores the previous image tag and commit
and exits non-zero (the workflow fails).

Manual, to any earlier build:

```bash
cd /opt/microcrm && ./scripts/deploy.sh <full-40-char-sha>   # re-deploys that commit's image (tag sha-<short> stays in GHCR)
```

Or re-run an older successful Deploy workflow run in GitHub Actions. Note that this deploys the old
image on the *current* database; migrations are not reversed.

## Restore a database backup

Only needed if a migration damaged data. This replaces the current database.

```bash
cd /opt/microcrm
ls -lt backups | head
docker compose -f compose.prod.yaml stop web celery beat
DB_USER=$(grep ^DB_USER= .env.prod | cut -d= -f2-); DB_NAME=$(grep ^DB_NAME= .env.prod | cut -d= -f2-)
docker compose -f compose.prod.yaml exec -T postgres psql -U "$DB_USER" -d postgres \
  -c "DROP DATABASE \"$DB_NAME\" WITH (FORCE)" -c "CREATE DATABASE \"$DB_NAME\" OWNER \"$DB_USER\""
gunzip -c backups/<file>.sql.gz | docker compose -f compose.prod.yaml exec -T postgres psql -U "$DB_USER" -d "$DB_NAME"
# deploy the matching older code/image (find the previous sha in deploy.log)
./scripts/deploy.sh <previous-full-sha>
```

## Building locally instead of pulling

`./scripts/deploy.sh --build <sha>` (or `docker compose -f compose.prod.yaml -f compose.build.yaml up --build -d`).
