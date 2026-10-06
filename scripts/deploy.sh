#!/usr/bin/env bash
# Routine update on the droplet, where /opt/pool-league-scheduler is a git
# clone: pull the repo, back up the database, pull the new image, recreate
# the container, and wait for the healthcheck to go green before calling it
# done. Run this from /opt/pool-league-scheduler (see docs/deploy.md).
#
# Usage:
#   ./scripts/deploy.sh                 routine update
#   ./scripts/deploy.sh --rollback TAG  deploy a specific image tag for this run
#
# TAG (for a pinned release, or a rollback) comes from .env or the shell
# environment: docker compose already reads .env in the project directory
# for variable substitution, so nothing here sources it by hand (which
# would risk echoing secrets).
set -euo pipefail

# Always operate on this script's own repo, whatever the caller's cwd.
cd "$(dirname "$0")/.."

ROLLBACK_TAG=""

while [ $# -gt 0 ]; do
    case "$1" in
        --rollback)
            if [ $# -lt 2 ]; then
                echo "deploy.sh: --rollback needs a tag argument" >&2
                exit 1
            fi
            ROLLBACK_TAG="$2"
            shift 2
            ;;
        *)
            echo "deploy.sh: unknown argument: $1" >&2
            exit 1
            ;;
    esac
done

if [ -n "$ROLLBACK_TAG" ]; then
    export TAG="$ROLLBACK_TAG"
    echo "deploy.sh: rolling back to TAG=$TAG for this run only."
    echo "deploy.sh: this does not persist -- set TAG=$TAG in .env yourself if you want it to stick."
fi

COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.prod.yml)

if [ -z "$ROLLBACK_TAG" ]; then
    # --- 1. refuse a dirty working tree ---------------------------------
    # A routine update should never pull over local edits to tracked files --
    # if something changed docker-compose.yml or scripts/*.sh by hand on the
    # droplet, that's a signal to go look, not to silently discard or merge it.
    if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
        echo "deploy.sh: working tree has local changes to tracked files -- refusing to pull." >&2
        echo "  git status --short" >&2
        git status --short >&2
        exit 1
    fi

    # --- 2. pull the repo -----------------------------------------------
    echo "deploy.sh: pulling repo (git pull --ff-only)..."
    git pull --ff-only
    REASON=deploy
else
    # Rollback is an emergency path: no dirty-tree check and no git pull, so
    # it works without GitHub and doesn't move the compose files to main.
    echo "deploy.sh: rollback -- skipping dirty-tree check and git pull."
    REASON=rollback
fi
export REASON

# --- 3. back up the database before touching the running container -----
if [ -f data/league.db ]; then
    echo "deploy.sh: backing up data/league.db (reason: $REASON)..."
    # data/ belongs to the container's uid 10002, not to whoever's running
    # this script, hence sudo. `env` passes REASON through sudo's env reset.
    sudo env REASON="$REASON" ./scripts/backup.sh data/league.db data/backups
else
    echo "deploy.sh: no database at data/league.db yet -- skipping backup (first deploy)."
fi

# --- 4. pull the image and recreate the container -----------------------
# docker compose reads TAG from .env on its own, but this shell doesn't,
# so without this the message below would say "latest" while compose
# pulled whatever .env pins. Only the TAG line is read.
ENV_TAG=""
if [ -z "${TAG:-}" ] && [ -f .env ]; then
    ENV_TAG="$(grep -E '^TAG=' .env | tail -n 1 | cut -d= -f2- | tr -d '"' || true)"
fi
# Refuse to start without a signing key (checked without printing it).
grep -Eq '^SECRET_KEY=.+' .env || { echo "SECRET_KEY missing/empty in .env" >&2; exit 1; }

# Remember what is running now so a failed deploy can say how to go back.
# Captured before `pull`, tolerating no running container.
PREV_IMAGE=""
PREV_CID="$("${COMPOSE[@]}" ps -q pool-league 2>/dev/null || true)"
if [ -n "$PREV_CID" ]; then
    PREV_IMAGE="$(docker inspect -f '{{.Config.Image}}' "$PREV_CID" 2>/dev/null || true)"
fi

print_recovery() {
    if [ -n "$PREV_IMAGE" ]; then
        local prev_tag="${PREV_IMAGE##*:}"
        echo "  previous image: $PREV_IMAGE" >&2
        echo "  to go back:     ./scripts/deploy.sh --rollback $prev_tag" >&2
        echo "  (a moving tag like 'latest' may already point at the new image after pull)" >&2
    else
        echo "  no previous container was running; no image to roll back to." >&2
    fi
}

echo "deploy.sh: pulling ${TAG:-${ENV_TAG:-latest}}..."
"${COMPOSE[@]}" pull

echo "deploy.sh: recreating container..."
"${COMPOSE[@]}" up -d

# --- 5. wait for healthy -------------------------------------------------
echo "deploy.sh: waiting for healthy status..."
ATTEMPTS=24  # 24 * 5s = 2 minutes
CID="$("${COMPOSE[@]}" ps -q pool-league)"

if [ -z "$CID" ]; then
    echo "deploy.sh: could not find the pool-league container after 'up -d'" >&2
    print_recovery
    exit 1
fi

STATUS="unknown"
for _ in $(seq 1 "$ATTEMPTS"); do
    STATUS="$(docker inspect -f '{{.State.Health.Status}}' "$CID" 2>/dev/null || echo "unknown")"
    if [ "$STATUS" = "healthy" ]; then
        echo "deploy.sh: container is healthy"
        "${COMPOSE[@]}" ps
        "${COMPOSE[@]}" logs --tail 50
        exit 0
    fi
    if [ "$STATUS" = "unhealthy" ]; then
        echo "deploy.sh: container reported unhealthy -- check logs:" >&2
        echo "  docker compose -f docker-compose.yml -f docker-compose.prod.yml logs --tail 100" >&2
        print_recovery
        exit 1
    fi
    sleep 5
done

echo "deploy.sh: still '$STATUS' after $((ATTEMPTS * 5))s -- not necessarily broken" >&2
echo "  (the healthcheck has a 20s start period), but check logs to be sure:" >&2
echo "  docker compose -f docker-compose.yml -f docker-compose.prod.yml logs --tail 100" >&2
print_recovery
exit 1
