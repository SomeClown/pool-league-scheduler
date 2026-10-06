# Deploy runbook: the Droplet

This is the "what do I actually type" document for running
pool-league-scheduler in production on the owner's DigitalOcean Droplet. The
app runs as a single Docker container behind the host's Nginx; the image is
built by CI and pulled from GHCR.

The Droplet is shared with discord-newsbot. **The two apps are fully
isolated:** separate compose projects, no shared networks or volumes, one
container each (two containers on the Droplet). Keep it that way; nothing in
this runbook should ever reference the bot's containers, `.env`, or `data/`.

Section 12 is the one-time migration from the old venv + systemd deploy to
the container. **Until that cutover is done, the Droplet still runs the venv
unit (`pool-league.service`) and sections 2 to 11 describe the target state,
not what is live.**

## 1. Prerequisites (one-time, on the Droplet)

The Droplet is Ubuntu 24.04. Docker Engine (minimum 20.10.10; older versions
break `python:3.14-slim`), the compose plugin (`docker compose`, two words),
`sqlite3`, and the deploy user's `docker` group membership are covered by the
discord-newsbot runbook, `docs/deploy.md` §1 in that repo
(`/Users/someclown/dev/discord-newsbot` locally); the bot's Droplet setup
already satisfies them. Verify rather than reinstall:

```bash
docker compose version                          # v2.x
docker version --format '{{.Server.Version}}'   # >= 20.10.10
sqlite3 --version                               # backup.sh needs it on the host
id -nG | tr ' ' '\n' | grep -x docker           # deploy user is in the docker group
```

`sudo apt install -y sqlite3` if the third is missing.

The Droplet's clock is UTC. The backup timer (section 8) names its own
timezone, so nothing here depends on the host clock.

`scripts/deploy.sh` runs `sudo ./scripts/backup.sh` (`data/` belongs to uid
10002, not the deploy user), so the deploy user needs sudo.

## 2. Directory layout

`/opt/pool-league-scheduler` **is a git clone of this repo**, owned by the same
deploy user that owns `/opt/newsbot`. A routine update is `git pull` plus
`docker compose pull && up -d` (what `scripts/deploy.sh` does), so there is no
second copy of the compose files to keep in sync. Because the deploy user owns
the checkout, `git pull` works without the old `chown root` / `chown www-data`
dance.

```
/opt/pool-league-scheduler/        # git clone of github.com/SomeClown/pool-league-scheduler
├── docker-compose.yml             # tracked: base (read-only root, tmpfs, logging)
├── docker-compose.prod.yml        # tracked: GHCR image, .env, ./data mount, loopback port
├── scripts/                       # tracked: deploy.sh, backup.sh
├── .env                           # gitignored: SECRET_KEY, TAG; chmod 600
└── data/                          # gitignored, owned by uid 10002
    ├── league.db                  # created by the container on first run
    └── backups/                   # created by scripts/backup.sh (owned by root)
```

`.env` and `data/` are covered by `.gitignore` (`.env`, `.env.*`, `data/`), so
`git pull` can neither overwrite nor delete them.

Fresh clone (only if you are not migrating an existing deploy; for that, see
section 12):

```bash
sudo mkdir -p /opt/pool-league-scheduler
sudo chown "$USER":"$USER" /opt/pool-league-scheduler
git clone https://github.com/SomeClown/pool-league-scheduler.git /opt/pool-league-scheduler
cd /opt/pool-league-scheduler
mkdir -p data
cp .env.example .env && chmod 600 .env     # then fill in SECRET_KEY
```

**The container runs as uid 10002** (fixed in the `Dockerfile`, not "the next
free uid", so this step survives rebuilds; 10002 because the bot's container
already owns 10001, and distinct uids keep the two apps out of each other's
data). Chown once:

```bash
sudo chown -R 10002:10002 /opt/pool-league-scheduler/data
```

If you skip this, the first write to `/data` inside the container fails and
the app never gets past startup. `/data` is the only writable place besides
`/tmp` (the root filesystem is read-only).

Dev on a Linux host needs `./data` writable by uid 10002 for the same reason;
Docker Desktop on macOS handles it.

### A file, not a directory

Before the first `docker compose up`, **`.env` must already exist.** The compose
file consumes it through `env_file`, and when it is missing compose refuses to
start with an "env file not found" error (it does not create anything in its
place). Create it first, then `up`.

`.env` needs `SECRET_KEY`. **If it is missing or empty the app silently falls
back to the development key in `config.py`**, which is signing sessions with a
public value. `scripts/deploy.sh` guards against this: it refuses to run if
`.env` lacks a non-empty `SECRET_KEY`. (The guard only covers deploys through
the script; a bare `docker compose up -d` has no such check.) Generate one:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

`DATABASE_URL` is not needed: `docker-compose.prod.yml` sets
`sqlite:////data/league.db` and that overrides anything in `.env` (compose
`environment` beats `env_file`). During the migration window the shared `.env`
still carries the legacy host-path `DATABASE_URL` for the venv; that is
harmless to the container and required by the venv rollback.

## 3. GHCR image access

`ghcr.io/someclown/pool-league-scheduler` is **public**, so `docker compose
pull` works with no login on the Droplet. Nothing to do here.

(If it ever goes private, log in once with a classic PAT scoped to
`read:packages` only: `docker login ghcr.io -u <github-username>`. Don't put
the PAT in `.env` or any committed file.)

CI (`.github/workflows/ci.yml`) pushes `linux/amd64` images on pushes to
`main` and `v*` tags. Tags: `latest` (main), `sha-<short>` (every build), and
semver (`1.2.3`, `1.2`) for tagged releases. The `GHCR_OWNER` variable
(default `someclown`) redirects the image reference if the repo ever moves.

## 4. First deploy

```bash
cd /opt/pool-league-scheduler
./scripts/deploy.sh
```

With no `data/league.db` yet it prints "skipping backup (first deploy)" and
carries on. Then create the first admin:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec pool-league \
  flask create-admin <username> <password>
```

Check it came up healthy (the healthcheck has a 20s start period, so
`starting` for a bit is normal):

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps
curl -sI http://127.0.0.1:8000/login | head -1     # HTTP/1.1 200 OK
```

The container publishes on `127.0.0.1:8000` only, deliberately: Docker's
published ports bypass UFW, so binding `0.0.0.0` would expose gunicorn to the
internet regardless of `ufw status`. Host Nginx proxies to it; see
`deploy/nginx.conf`, a reference template (the live config is certbot-managed;
never copy the file over it).

## 5. Routine updates

```bash
cd /opt/pool-league-scheduler
./scripts/deploy.sh
```

In order, it:

1. Refuses to run if `.env` lacks a non-empty `SECRET_KEY`.
2. Refuses to run if tracked files have local changes (a hand-edit on the
   Droplet is a signal to look, not to merge over).
3. `git pull --ff-only`.
4. Backs up `data/league.db` via `sudo ./scripts/backup.sh data/league.db
   data/backups` (skipped if there is no database yet).
5. `docker compose -f docker-compose.yml -f docker-compose.prod.yml pull`,
   then `up -d`, honoring `TAG` from `.env` or the environment.
6. Waits up to 2 minutes for the container to report `healthy`, then prints
   `ps` and the last 50 log lines. Exits non-zero on `unhealthy` or timeout;
   the failure message includes the previously-running image tag as a
   ready-made `./scripts/deploy.sh --rollback <tag>` command. It does not roll
   back automatically.

**After tagging a release, wait for CI to finish before deploying.** Run it
too early and the pull fails with `manifest unknown`; nothing breaks (the
script stops before touching the running container). `gh run list --limit 3`
shows when the build is done.

Recommended: pin `TAG` in `.env` to a release (e.g. `TAG=1.2.0`) rather than
floating on `latest`, so "what's running" and "how do I undo it" are
one-line answers.

Deploys do not run migrations. If a release adds model columns, run `flask
db-migrate` once the container is up (section 7); it is idempotent, so running
it when unsure is safe.

## 6. Rollback

By tag:

```bash
cd /opt/pool-league-scheduler
./scripts/deploy.sh --rollback 1.1.0      # or sha-abc1234
```

`--rollback` sets `TAG` for that one run only; set `TAG=` in `.env` yourself to
make it stick (the script does not edit `.env`). It is the emergency path, so
it skips the dirty-tree check and the `git pull`: no GitHub dependency, and
the compose files and scripts stay exactly as they are on disk. It still takes
a database backup (reason `rollback`, section 8) before swapping the image.
The backup is the safety net if the bad release included a schema change:
images roll back, the database does not, so restore from `data/backups/`
(section 8) if the older image can't read the migrated database.

To go back to the pre-container venv deploy, see section 12 (rollback paths).
That is only possible until the decommission in section 12 is done.

## 7. Running flask CLI commands

`FLASK_APP=app` is set in the image (`.flaskenv` is not shipped in the image),
so the commands in `app/__init__.py` work as-is: `create-admin`,
`make-superuser`, `db-migrate`, `seed-league-types` (and the one-off
`migrate-f16`).

Container up:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec pool-league \
  flask make-superuser <username>
```

Container down (stopped, or a first-time setup before `up`): `run --rm`
starts a throwaway container with the same volumes and env. It does not
publish ports.

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml run --rm pool-league \
  flask db-migrate
```

Both run as uid 10002 against `/data/league.db`.

## 8. Backups

`scripts/backup.sh` takes an online-safe snapshot with `sqlite3 .backup`
(unlike `cp`, it can't catch the file mid-write), checks `pragma
integrity_check` on the result. Files are named
`data/backups/league-<YYYY-MM-DD-HHMMSS>-<reason>.db`, where reason is
`nightly` (the timer), `deploy` (`deploy.sh`) or `rollback`
(`deploy.sh --rollback`), e.g. `league-2026-10-05-033000-nightly.db`.
Retention is count-based: it keeps the 14 newest files regardless of reason,
so a burst of deploys can push older nightlies out. Backups live on the same host as the
database: they protect against bad edits and bad migrations, not against
losing the Droplet (that's what DigitalOcean snapshots are for).

Install the systemd timer (once, and again if the unit files change; systemd
uses its own copies under `/etc/systemd/system/`, not the repo's):

```bash
sudo cp /opt/pool-league-scheduler/deploy/systemd/pool-league-backup.service \
        /opt/pool-league-scheduler/deploy/systemd/pool-league-backup.timer \
        /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pool-league-backup.timer
```

Verify:

```bash
systemctl list-timers pool-league-backup.timer
sudo systemctl start pool-league-backup.service       # run it once by hand
journalctl -u pool-league-backup.service --since today
ls -l /opt/pool-league-scheduler/data/backups/
```

The timer fires at `*-*-* 03:30:00 America/Los_Angeles` (systemd resolves DST
itself; `Persistent=true` catches up a missed run at next boot). The service
runs as root, because `data/` belongs to uid 10002, and does nothing if
`data/league.db` doesn't exist yet.

### Restore from backup

1. Stop the container so nothing writes mid-restore:
   ```bash
   docker compose -f docker-compose.yml -f docker-compose.prod.yml stop
   ```
2. Move the current database aside rather than deleting it, and **remove the
   SQLite sidecar files**; a stale `-journal`/`-wal`/`-shm` next to a restored
   database can be replayed against it and corrupt it:
   ```bash
   sudo mv data/league.db data/league.db.pre-restore-$(date +%F)
   sudo rm -f data/league.db-journal data/league.db-wal data/league.db-shm
   ```
3. Copy the chosen backup into place and **give it back to uid 10002** (a
   `sudo cp` leaves it root-owned, and the container then can't write it):
   ```bash
   sudo cp data/backups/league-2026-10-05-033000-nightly.db data/league.db
   sudo chown 10002:10002 data/league.db
   ```
4. Sanity-check it:
   ```bash
   sqlite3 data/league.db "pragma integrity_check;"     # ok
   ```
5. Start the container and confirm the site loads:
   ```bash
   docker compose -f docker-compose.yml -f docker-compose.prod.yml start
   ```

A restore rolls the database back to the backup's point in time: schedules,
admin accounts and password changes since then are lost. It does not touch
`.env` or the image.

## 9. Logs

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs --tail 100
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs -f
```

Gunicorn (access and error) and the app's `logger.exception(...)` tracebacks
all go to the container's stdout/stderr (the `CMD` overrides the log paths in
`gunicorn.conf.py`). `docker-compose.yml` sets `json-file` logging at 10m x 3
files, rotated automatically. **`/var/log/pool-league/` is retired**: nothing
writes there from the container. The old venv unit's logs stay in it until
decommission. Backup-job output is in the journal, not here:
`journalctl -u pool-league-backup.service`.

## 10. Rotating SECRET_KEY

1. Generate a new key (command in section 2) and update
   `/opt/pool-league-scheduler/.env`.
2. Recreate the container so it picks up the new environment:
   ```bash
   docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
   ```
   (`restart` is not enough; it doesn't re-read `.env`.)

**This logs every user out**: Flask sessions are signed with the key, so all
existing sessions become invalid. Passwords are stored as hashes and are
unaffected. Never commit `.env` or paste its contents anywhere.

## 11. Troubleshooting

- **Container restarts or exits right away, logs show a permission/readonly
  error on `/data`:** `data/` isn't owned by uid 10002 (section 2).
- **compose says "env file not found":** `.env` doesn't exist (section 2);
  create it from `.env.example` and run again.
- **`deploy.sh` refuses with a `SECRET_KEY` error:** `.env` has no non-empty
  `SECRET_KEY` (section 2); generate one and add it.
- **Everyone is logged out after a deploy:** `SECRET_KEY` changed in `.env`.
  The probe in section 12 step 10 tells you which key is loaded.
- **502 from Nginx:** container not up or not healthy; `ps` and `logs`.
- **App thinks requests are plain HTTP (wrong scheme in redirects/URLs, HTTPS
  not detected):** gunicorn only honors `X-Forwarded-Proto` from trusted
  peers, and `FORWARDED_ALLOW_IPS` is set to `"*"` in `docker-compose.yml`
  because requests reach gunicorn from the Docker bridge gateway, not
  127.0.0.1. Safe only because the port is loopback-published; don't change
  one without the other. This setting is not about client addresses:
  gunicorn's logs show the bridge IP (172.x) either way; real client IPs are
  in the host Nginx access log.
- **`manifest unknown` on pull:** CI hasn't finished building that tag yet.
- **Known follow-up:** `requirements.txt` pins gunicorn 22.0.0, which has a
  published request-smuggling advisory. Bumping it is a separate change, not
  part of the containerization.

## 12. One-time migration: venv + systemd to container

Run once, by the owner, on the Droplet. Expect a few minutes of downtime at
the cutover step only; everything before it happens while the venv site stays
live.

State before: gunicorn in `/opt/pool-league-scheduler/venv` (rebuilt on Python
3.12 on 2026-10-06 and currently serving the site, so rolling back to it is
verified working), run by `pool-league.service` as `www-data`, database at
`/opt/pool-league-scheduler/league.db`, files owned by `www-data`, `/static/`
served by an Nginx `alias`.

**Before you start:** the containerization change (Dockerfile, compose files,
scripts, `deploy/`) must be merged to `main` and CI must be green with the
image pushed (`gh run list --limit 3`); otherwise the pull in step 8 has
nothing to fetch.

### Pre-flight

1. See what the unit actually sets, since the container must reproduce it:
   ```bash
   systemctl cat pool-league       # note any Environment= / EnvironmentFile= lines
   ```
2. List the `.env` key names without printing values:
   ```bash
   sed 's/=.*//' /opt/pool-league-scheduler/.env
   ```
   The container needs `SECRET_KEY` (copied verbatim, so existing sessions
   survive). Anything else the app reads is in `config.py`.
3. Find the live database and check it. The expected path is
   `/opt/pool-league-scheduler/league.db` (the `DATABASE_URL` in `.env`;
   confirm):
   ```bash
   sqlite3 /opt/pool-league-scheduler/league.db "pragma integrity_check;"   # ok
   sqlite3 /opt/pool-league-scheduler/league.db "pragma journal_mode;"      # expect: delete
   ```
   `delete` means a clean `.backup` copy is a complete picture. If it says
   `wal`, stop and rethink: the copy steps below still work (`.backup` is
   WAL-aware) but the restore notes about sidecar files matter more.
4. Check the live Nginx site (certbot-managed) so you know exactly which
   `/static/` block to edit later, and keep a copy for rollback:
   ```bash
   cat /etc/nginx/sites-available/pool-league
   sudo cp /etc/nginx/sites-available/pool-league /root/pool-league.nginx.pre-container
   ```
5. Check Docker, the bot, and the firewall:
   ```bash
   docker compose version
   docker ps                       # the bot's container should be running
   sudo ufw status                 # port 8000 should not be open; it won't need to be
   id -nG                          # deploy user is in the docker group
   ```
6. Port 8000 is currently held by the venv unit. The rehearsal below uses
   8001; the real container can't start until the unit is stopped.

### Safety net

7. Take a DigitalOcean snapshot of the Droplet (control panel; last-resort
   rollback). Then a database copy outside the repo:
   ```bash
   sudo sqlite3 /opt/pool-league-scheduler/league.db \
     ".backup '/root/league-pre-container-$(date +%F).db'"
   sudo sqlite3 /root/league-pre-container-$(date +%F).db "pragma integrity_check;"
   ```

### Stage while the site is live

8. Pull the repo once more using the old ownership dance (the last time it's
   needed; the tree is still owned by `www-data`), and pull the image:
   ```bash
   cd /opt/pool-league-scheduler
   sudo chown -R root:root .
   sudo git pull
   sudo chown -R www-data:www-data .       # restore so the venv unit keeps working
   sudo mkdir -p data
   docker pull ghcr.io/someclown/pool-league-scheduler:latest   # or the release tag you will pin
   ```
   This uses a plain `docker pull` rather than `docker compose pull` because
   compose reads `env_file` `.env` even for `pull`, and by now `.env` is a
   `600` file owned by `www-data`, which the deploy user can't read. It
   fetches the image only; it doesn't start anything.
9. Rehearse the image against a **scratch copy** of the database on port 8001.
   This uses plain `docker run` so it can't collide with the real compose
   project or port 8000:
   ```bash
   sudo mkdir -p /root/rehearsal-data
   sudo sqlite3 /opt/pool-league-scheduler/league.db ".backup '/root/rehearsal-data/league.db'"
   sudo chown -R 10002:10002 /root/rehearsal-data

   SK=$(sudo grep '^SECRET_KEY=' /opt/pool-league-scheduler/.env | cut -d= -f2-)
   docker run --rm -d --name pool-rehearsal \
     --read-only --tmpfs /tmp --security-opt no-new-privileges:true \
     -e SECRET_KEY="$SK" -e DATABASE_URL=sqlite:////data/league.db \
     -e FORWARDED_ALLOW_IPS='*' \
     -v /root/rehearsal-data:/data \
     -p 127.0.0.1:8001:8000 \
     ghcr.io/someclown/pool-league-scheduler:latest

   sleep 25
   docker ps --filter name=pool-rehearsal       # STATUS: healthy
   curl -sI http://127.0.0.1:8001/login | head -1
   docker exec pool-rehearsal flask db-migrate  # against the copy
   docker logs pool-rehearsal --tail 30
   ```
   Open it through an SSH tunnel (`ssh -L 8001:127.0.0.1:8001 <droplet>`) and
   log in, view a season, and try an Excel export. Then tear down:
   ```bash
   unset SK
   docker stop pool-rehearsal
   sudo rm -rf /root/rehearsal-data
   ```
   Do not proceed if the rehearsal isn't healthy or db-migrate errors; the
   real site is untouched at this point.

### Cutover (downtime starts)

10. Run in order:
    ```bash
    sudo systemctl stop pool-league
    sudo systemctl disable pool-league

    cd /opt/pool-league-scheduler
    # consistent copy of the live DB into the container's data dir
    sudo mkdir -p data
    sudo sqlite3 league.db ".backup 'data/league.db'"
    sudo sqlite3 data/league.db "pragma integrity_check;"      # ok

    # ownership: repo to the deploy user (same as /opt/newsbot), data to the container uid
    sudo chown -R <deploy-user>:<deploy-user> /opt/pool-league-scheduler
    sudo chown -R 10002:10002 data
    chmod 600 .env
    printf '\nTAG=latest\n' >> .env     # or pin a release, e.g. TAG=1.0.0; leading \n in case .env lacks a trailing newline

    ./scripts/deploy.sh
    ```
    Leave the old `league.db` in the repo root, untouched; it is the venv
    rollback's database. Leave `DATABASE_URL` in `.env` too (the compose
    override ignores it; the venv rollback needs it).

    Verify:
    ```bash
    C="docker compose -f docker-compose.yml -f docker-compose.prod.yml"
    $C exec pool-league flask db-migrate         # twice if you like; idempotent, no changes
    # SECRET_KEY probe: exit 0 means a real key is loaded, 1 means the dev fallback
    $C exec pool-league python -c "import sys; from config import Config; sys.exit(int(Config.SECRET_KEY == 'dev-secret-change-in-production'))"; echo $?
    curl -s http://127.0.0.1:8000/sw.js | head -3
    curl -s http://127.0.0.1:8000/manifest.json | head -3
    ```
    Then in a browser:
    - an **existing logged-in session is still valid** (same `SECRET_KEY`), or
      log in again if you were logged out
    - open a season and download the xlsx export
    - **write test:** add and then remove a blackout date on a non-critical
      season (exercises writes under the read-only root and `/data`)

### Nginx /static/ change

11. Proxy `/static/` to the container instead of serving the checkout's
    `app/static` via the old `alias`. The droplet stays a git clone, so the
    alias would keep working, but proxying keeps static assets version-matched
    to the running image when the image `TAG` and the checkout differ (e.g.
    after a rollback by `TAG`). Edit the **live** config
    by hand (don't copy `deploy/nginx.conf` over it); replace the `location
    /static/ { alias ...; ... }` block with the block from `deploy/nginx.conf`:
    ```nginx
    location /static/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        expires 7d;
    }
    ```
    Do this in both the 80 and 443 server blocks if the live config has both.
    ```bash
    sudo nginx -t && sudo systemctl reload nginx
    ```
    Reload the site: styled pages, icons, and `/static/sw.js` content load.
    Downtime ends here.

### Install the backup timer

12. Per section 8: `cp` the two units, `daemon-reload`, `enable --now`, then
    `sudo systemctl start pool-league-backup.service` once and confirm
    a `data/backups/league-<today>-<time>-<reason>.db` file exists.

### Rollback paths

- **Container is up but a release is bad:** section 6
  (`./scripts/deploy.sh --rollback <tag>`).
- **Back to the venv (verified working on 2026-10-06):**
  ```bash
  cd /opt/pool-league-scheduler
  docker compose -f docker-compose.yml -f docker-compose.prod.yml down
  # carry any writes made since cutover back to the venv's database
  sudo mv league.db league.db.pre-rollback-$(date +%F)
  sudo rm -f league.db-journal league.db-wal league.db-shm
  sudo sqlite3 data/league.db ".backup 'league.db'"
  sudo chown -R www-data:www-data /opt/pool-league-scheduler
  # restore the old /static/ alias in the live nginx config (from
  # /root/pool-league.nginx.pre-container), then:
  sudo nginx -t && sudo systemctl reload nginx
  sudo systemctl enable --now pool-league
  ```
  Port 8000 must be free, hence `down` before starting the unit.
- **Last resort:** restore the DigitalOcean snapshot from step 7 (loses
  anything done on the Droplet since, including the bot's data; prefer the
  paths above).

### Decommission (after a two-week soak)

Not before two weeks of the container serving the site cleanly, which is also
the window in which the nightly backups prove themselves. After that:

```bash
sudo rm /etc/systemd/system/pool-league.service      # already stopped and disabled
sudo systemctl daemon-reload
cd /opt/pool-league-scheduler
rm -rf venv                                          # deploy user owns the tree now
# keep league.db and league.db.pre-* for another cycle, then delete; remove the
# legacy DATABASE_URL line from .env at the same time
```

`/var/log/pool-league/` and the pre-cutover copies in `/root/` can be removed
at the same point. `deploy/pool-league.service` stays in the repo until a
human decides to delete it; once the venv is gone, so is the rollback it
serves.
