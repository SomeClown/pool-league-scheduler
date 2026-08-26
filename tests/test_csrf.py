"""
tests/test_csrf.py — CSRF enforcement (Flask-WTF's CSRFProtect).

Every other test file relies on the shared `app`/`client` fixtures in
conftest.py, which deliberately set WTF_CSRF_ENABLED = False so form POSTs
don't need to scrape a token out of rendered HTML (see conftest's module
docstring). This file is the one place CSRF actually matters, so it builds
its own dedicated app instance with CSRF left ON rather than touching the
shared fixtures.
"""

import re

import pytest

from app import create_app, db
from tests.conftest import BaseTestConfig, create_user


def _extract_csrf_token(html):
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, 'no csrf_token field found in the rendered login form'
    return match.group(1)


@pytest.fixture
def csrf_app(tmp_path):
    db_path = tmp_path / 'csrf_test_league.db'

    class CsrfTestConfig(BaseTestConfig):
        SQLALCHEMY_DATABASE_URI = 'sqlite:///' + str(db_path)
        WTF_CSRF_ENABLED = True  # the whole point of this file

    application = create_app(CsrfTestConfig)
    yield application

    with application.app_context():
        db.session.remove()
        db.engine.dispose()


def test_post_without_csrf_token_is_rejected(csrf_app):
    create_user(csrf_app, 'csrf-admin', 'csrf-password1', role='admin')
    client = csrf_app.test_client()

    # Log in for real first (the login form carries a genuine token), so the
    # rejection we're about to see is specifically about the missing CSRF
    # token on the next request, not about authentication.
    login_page = client.get('/login')
    token = _extract_csrf_token(login_page.get_data(as_text=True))
    login_response = client.post('/login', data={
        'username': 'csrf-admin', 'password': 'csrf-password1', 'csrf_token': token,
    })
    assert login_response.status_code == 302

    # Now hit an admin POST route with no csrf_token field at all.
    response = client.post('/admin/bars/add', data={'name': 'No Token Bar', 'tables': '2'})
    assert response.status_code == 400
