# Multi-stage build: the builder stage creates the venv and installs the
# dependencies, and the runtime stage copies only the finished venv and the
# app source across. pip's cache and any build tooling stay behind in the
# builder, so they never ship.

# ---- builder ----------------------------------------------------------
FROM python:3.14-slim AS builder

WORKDIR /app

RUN python -m venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH

# requirements.txt on its own, before any source: the (slow) dependency
# layer is only rebuilt when the pins change, not on every template tweak.
# There is no pyproject/setup.py here -- the app runs straight from its
# source tree, so there is nothing to `pip install .`.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ---- runtime ------------------------------------------------------------
FROM python:3.14-slim AS runtime

# uid 10002 is fixed (not "the next free uid") so the bind-mounted ./data
# on the host can be chown'd once, in the runbook, and keep working across
# rebuilds. 10002 rather than 10001 because the newsbot container on the
# same droplet already owns 10001; distinct uids keep the two apps from
# being able to touch each other's data.
RUN useradd --uid 10002 --no-create-home --shell /usr/sbin/nologin pool \
    && mkdir /data \
    && chown 10002:10002 /data

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY app ./app
COPY config.py run.py gunicorn.conf.py ./

# PYTHONDONTWRITEBYTECODE: the compose file runs this image with a
# read-only root filesystem, so Python could not write .pyc files anyway;
# this stops it from trying (and from logging about it).
# FLASK_APP=app: .flaskenv is excluded from the image (see .dockerignore),
# and `flask create-admin` and friends need to know where the app lives.
# TMPDIR=/tmp: SQLite and Python both put temp files here; compose mounts a
# tmpfs at /tmp, which is the one writable scratch spot besides /data.
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    FLASK_APP=app \
    TMPDIR=/tmp

USER pool

EXPOSE 8000

# /manifest.json is a plain public route (no login redirect, no database
# hit), so a 200 there means gunicorn is up and serving the Flask app.
# urllib raises on any non-2xx, which is what makes the check fail.
# 127.0.0.1 is correct from inside the container: gunicorn binds 0.0.0.0.
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/manifest.json', timeout=3)"

# The flags below override gunicorn.conf.py, which stays untouched because
# the old systemd deploy still reads it:
#   --bind 0.0.0.0:8000   the conf binds 127.0.0.1, unreachable through
#                         Docker's port publishing from outside the container
#   --worker-tmp-dir      gunicorn's worker heartbeat files go to /dev/shm
#                         (RAM) since the root filesystem is read-only
#   --access/error-logfile -   the conf writes to /var/log/pool-league/,
#                         which doesn't exist here; stdout/stderr feed
#                         `docker compose logs`
CMD ["gunicorn", "--config", "gunicorn.conf.py", "--bind", "0.0.0.0:8000", "--worker-tmp-dir", "/dev/shm", "--access-logfile", "-", "--error-logfile", "-", "run:app"]
