"""
tests/test_first_boot_race.py — Regression tests for the first-boot
create_all() race between gunicorn workers.

Regression (2026-10-06): with 2 gunicorn workers and a brand-new empty SQLite
database, both workers ran db.create_all() inside create_app() at the same
time. Both inspected the schema, saw no tables, and raced to CREATE TABLE —
the loser crashed with OperationalError ("table ... already exists" or
"database is locked") and gunicorn exited 3 with "Worker failed to boot".
Reruns succeeded because the winner had already created the tables.

The fix (_create_tables_tolerating_concurrent_boot in app/__init__.py)
retries create_all() when it fails with one of those two race signatures,
and re-raises anything else. The race window is timing-dependent, so these
tests exercise the handler directly by injecting the exact OperationalError
shapes SQLite produces, rather than trying to reproduce the race with real
concurrent processes.

All code here must stay Python 3.8 compatible — the production server runs 3.8.
"""

import pytest
from sqlalchemy.exc import OperationalError

from app import _create_tables_tolerating_concurrent_boot, db


def _operational_error(message):
    """Build an OperationalError shaped like SQLAlchemy raises from SQLite."""
    return OperationalError('CREATE TABLE users (...)', {}, Exception(message))


def _install_failing_create_all(monkeypatch, failures):
    """
    Replace db.create_all with a stub that raises each exception in
    `failures` (one per call) and then succeeds. Returns a list recording
    one entry per call, so tests can assert how many attempts were made.
    """
    calls = []
    remaining = list(failures)

    def fake_create_all(*args, **kwargs):
        calls.append(True)
        if remaining:
            raise remaining.pop(0)

    monkeypatch.setattr(db, 'create_all', fake_create_all)
    return calls


def test_already_exists_from_concurrent_worker_is_retried(app, monkeypatch):
    """The losing worker's "table already exists" must not kill the boot."""
    calls = _install_failing_create_all(
        monkeypatch, [_operational_error('table users already exists')])
    with app.app_context():
        _create_tables_tolerating_concurrent_boot(app, wait_seconds=0)
    assert len(calls) == 2, 'expected one failed attempt plus one retry'


def test_database_locked_by_concurrent_worker_is_retried(app, monkeypatch):
    """SQLite's "database is locked" during the race must also be retried."""
    calls = _install_failing_create_all(
        monkeypatch, [_operational_error('database is locked')])
    with app.app_context():
        _create_tables_tolerating_concurrent_boot(app, wait_seconds=0)
    assert len(calls) == 2, 'expected one failed attempt plus one retry'


def test_unrelated_operational_error_still_raises(app, monkeypatch):
    """A genuinely broken database must fail the boot loudly, not retry."""
    _install_failing_create_all(
        monkeypatch, [_operational_error('unable to open database file')])
    with app.app_context():
        with pytest.raises(OperationalError):
            _create_tables_tolerating_concurrent_boot(app, wait_seconds=0)


def test_persistent_race_error_raises_after_retries(app, monkeypatch):
    """If every attempt fails, the last error propagates instead of looping."""
    error = _operational_error('database is locked')
    calls = _install_failing_create_all(monkeypatch, [error, error, error])
    with app.app_context():
        with pytest.raises(OperationalError):
            _create_tables_tolerating_concurrent_boot(
                app, attempts=3, wait_seconds=0)
    assert len(calls) == 3, 'expected exactly attempts=3 calls'


def test_fresh_database_still_gets_created_automatically(tmp_path):
    """
    The behavior the fix must preserve: create_app() against a brand-new
    empty database still creates the schema with no manual step.
    """
    from app import create_app
    from app.models import User

    db_path = tmp_path / 'fresh_boot.db'

    class FreshBootConfig:
        TESTING = True
        SECRET_KEY = 'test-secret-key'
        SQLALCHEMY_TRACK_MODIFICATIONS = False
        WTF_CSRF_ENABLED = False
        SQLALCHEMY_DATABASE_URI = 'sqlite:///' + str(db_path)

    application = create_app(FreshBootConfig)
    try:
        with application.app_context():
            # Any query working at all proves the tables exist.
            assert User.query.count() == 0
    finally:
        with application.app_context():
            db.session.remove()
            db.engine.dispose()
