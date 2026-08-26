"""
tests/conftest.py — Shared pytest fixtures.

Every test gets a fresh Flask app bound to a temporary SQLite database file
(one per test, via pytest's tmp_path), so tests never touch the real
league.db and never depend on each other's state.

CSRF protection is disabled in the test config (WTF_CSRF_ENABLED = False) so
form POSTs don't need to scrape a token out of rendered HTML. Auth and role
enforcement are still fully active.

All code here must stay Python 3.8 compatible — the production server runs 3.8.
"""

import pytest

from app import create_app, db
from app.models import Bar, Season, Team, User


ADMIN_USERNAME = 'test-admin'
ADMIN_PASSWORD = 'admin-secret-pw'
VIEWER_USERNAME = 'test-viewer'
VIEWER_PASSWORD = 'viewer-secret-pw'
SUPERUSER_USERNAME = 'test-superuser'
SUPERUSER_PASSWORD = 'superuser-secret-pw'


class BaseTestConfig:
    TESTING = True
    SECRET_KEY = 'test-secret-key'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    # SQLALCHEMY_DATABASE_URI is filled in per-test by the app fixture.


@pytest.fixture
def app(tmp_path):
    """A fresh app instance backed by a temporary SQLite database file."""
    db_path = tmp_path / 'test_league.db'

    class TestConfig(BaseTestConfig):
        # str(db_path) is absolute, so this yields sqlite:////... (4 slashes).
        SQLALCHEMY_DATABASE_URI = 'sqlite:///' + str(db_path)

    application = create_app(TestConfig)
    yield application

    with application.app_context():
        db.session.remove()
        db.engine.dispose()


@pytest.fixture
def client(app):
    """An anonymous (not logged in) test client."""
    return app.test_client()


@pytest.fixture(autouse=True)
def _enforce_temp_database(request, tmp_path):
    """
    Fail loudly if a test's app is not bound to the per-test tmp_path database.

    The `app` fixture always builds its config with a tmp_path-scoped SQLite
    URI, but that's a convention, not an invariant — nothing stops a future
    test (or a future edit to the `app` fixture itself) from calling
    create_app() with no config and landing on the real league.db. This
    fixture is the backstop: it inspects whatever app the test actually
    built and asserts the URI is rooted under this test's tmp_path.

    Skips entirely for tests that never request the `app` fixture (directly
    or via client/admin_client/viewer_client/sample_league) — e.g. the pure
    scheduler-algorithm tests, which never touch Flask or a database, so
    there's nothing to check and no reason to force an app to be built.
    """
    if 'app' not in request.fixturenames:
        return
    application = request.getfixturevalue('app')
    uri = application.config['SQLALCHEMY_DATABASE_URI']
    expected_prefix = 'sqlite:///' + str(tmp_path)
    assert uri.startswith(expected_prefix), (
        'Refusing to run: SQLALCHEMY_DATABASE_URI ({0!r}) is not scoped to '
        'this test\'s tmp_path ({1}) — a test in this state could read or '
        'write the real league.db.'.format(uri, tmp_path)
    )


def create_user(app, username, password, role='viewer', is_superuser=False):
    """Create and commit a user; returns its id."""
    with app.app_context():
        user = User(username=username, role=role, is_superuser=is_superuser)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        return user.id


def login(client, username, password):
    """POST the login form. Returns the response (302 on success)."""
    return client.post('/login', data={'username': username, 'password': password})


@pytest.fixture
def admin_client(app):
    """A test client logged in as an admin-role user."""
    create_user(app, ADMIN_USERNAME, ADMIN_PASSWORD, role='admin')
    test_client = app.test_client()
    response = login(test_client, ADMIN_USERNAME, ADMIN_PASSWORD)
    assert response.status_code == 302, 'admin login failed during fixture setup'
    return test_client


@pytest.fixture
def viewer_client(app):
    """A test client logged in as a viewer-role user."""
    create_user(app, VIEWER_USERNAME, VIEWER_PASSWORD, role='viewer')
    test_client = app.test_client()
    response = login(test_client, VIEWER_USERNAME, VIEWER_PASSWORD)
    assert response.status_code == 302, 'viewer login failed during fixture setup'
    return test_client


@pytest.fixture
def superuser_client(app):
    """A test client logged in as the superuser (role='admin', is_superuser=True)."""
    create_user(app, SUPERUSER_USERNAME, SUPERUSER_PASSWORD, role='admin', is_superuser=True)
    test_client = app.test_client()
    response = login(test_client, SUPERUSER_USERNAME, SUPERUSER_PASSWORD)
    assert response.status_code == 302, 'superuser login failed during fixture setup'
    return test_client


@pytest.fixture
def sample_league(app):
    """
    Two bars and four teams, two per bar.

    Each bar has more physical tables (4) than its standing tables_in_use
    limit (2) — deliberately different so tests that assert on the clamped
    cap value can tell "used tables_in_use" apart from "used tables" (a bug
    that would otherwise be invisible if the two numbers matched).

    Returns a dict of plain ids/names so tests never hold detached ORM
    instances: {'bar_ids': [...], 'team_ids': [...], 'team_names': [...]}.
    """
    with app.app_context():
        bar_a = Bar(name='The Cue Club', tables=4, tables_in_use=2)
        bar_b = Bar(name='Rack Room', tables=4, tables_in_use=2)
        db.session.add_all([bar_a, bar_b])
        db.session.flush()

        teams = [
            Team(name='Sharks', bar_id=bar_a.id),
            Team(name='Hustlers', bar_id=bar_a.id),
            Team(name='Breakers', bar_id=bar_b.id),
            Team(name='Bank Shots', bar_id=bar_b.id),
        ]
        db.session.add_all(teams)
        db.session.commit()

        return {
            'bar_ids': [bar_a.id, bar_b.id],
            'team_ids': [t.id for t in teams],
            'team_names': [t.name for t in teams],
        }


@pytest.fixture
def create_season(admin_client, app):
    """
    Factory fixture: POST the /seasons/new form and return the new season's
    id. Centralizes the form-shape boilerplate that would otherwise be
    copy-pasted across every wave-3 test file that needs a real, persisted,
    fully-scheduled season to act on (export, regenerate, blackouts, ...).

    Raises an assertion error immediately if creation didn't redirect (i.e.
    validation failed), so a broken test setup fails at the fixture call
    site rather than silently producing a missing season deeper in the test.
    """
    def _create(name, team_ids, start_date='2026-09-01', frequency='weekly',
                num_weeks=None, end_date=None, blackout_dates=None,
                bar_tables=None, league_type_id=None):
        form = {
            'name': name,
            'start_date': start_date,
            'frequency': frequency,
            'team_ids': [str(tid) for tid in team_ids],
        }
        if num_weeks is not None:
            form['length_mode'] = 'num_weeks'
            form['num_weeks'] = str(num_weeks)
        else:
            form['length_mode'] = 'end_date'
            form['end_date'] = end_date
        if blackout_dates:
            form['blackout_date'] = list(blackout_dates)
        if league_type_id is not None:
            form['league_type_id'] = str(league_type_id)
        if bar_tables:
            for bar_id, tables in bar_tables.items():
                form['bar_tables_{0}'.format(bar_id)] = str(tables)

        response = admin_client.post('/seasons/new', data=form)
        assert response.status_code == 302, (
            'season creation failed during test setup (form re-rendered '
            'instead of redirecting): {0!r}'.format(response.data[:500]))

        with app.app_context():
            season = Season.query.filter_by(name=name).first()
            assert season is not None, 'season row not found after creation'
            return season.id
    return _create
