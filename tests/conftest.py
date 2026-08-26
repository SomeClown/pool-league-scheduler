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
from app.models import Bar, Team, User


ADMIN_USERNAME = 'test-admin'
ADMIN_PASSWORD = 'admin-secret-pw'
VIEWER_USERNAME = 'test-viewer'
VIEWER_PASSWORD = 'viewer-secret-pw'


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
def sample_league(app):
    """
    Two bars (2 tables each) and four teams, two per bar.

    Returns a dict of plain ids/names so tests never hold detached ORM
    instances: {'bar_ids': [...], 'team_ids': [...], 'team_names': [...]}.
    """
    with app.app_context():
        bar_a = Bar(name='The Cue Club', tables=2, tables_in_use=2)
        bar_b = Bar(name='Rack Room', tables=2, tables_in_use=2)
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
