"""
tests/test_auth.py — Login/logout flow and access control on protected routes.

Verifies three layers of behavior:
    1. Anonymous users are redirected to /login on @login_required routes.
    2. Viewer-role users get 403 on @admin_required routes (read pages and
       mutation endpoints alike).
    3. The login/logout flow itself works and actually changes what a
       client can reach.
"""

import pytest

from tests.conftest import (
    ADMIN_PASSWORD,
    ADMIN_USERNAME,
    create_user,
    login,
)


PROTECTED_ADMIN_ROUTES_GET = ['/seasons/new', '/admin']
PROTECTED_ADMIN_ROUTES_POST = [
    '/admin/bars/add',
    '/admin/teams/add',
    '/admin/users/add',
]


# ---------------------------------------------------------------------------
# Anonymous access
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('path', PROTECTED_ADMIN_ROUTES_GET)
def test_anonymous_get_on_protected_route_redirects_to_login(client, path):
    response = client.get(path)
    assert response.status_code == 302
    assert '/login' in response.headers['Location']


@pytest.mark.parametrize('path', PROTECTED_ADMIN_ROUTES_POST)
def test_anonymous_post_on_admin_mutation_redirects_to_login(client, path):
    response = client.post(path, data={})
    assert response.status_code == 302
    assert '/login' in response.headers['Location']


# ---------------------------------------------------------------------------
# Viewer role restrictions
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('path', PROTECTED_ADMIN_ROUTES_GET)
def test_viewer_gets_403_on_admin_pages(viewer_client, path):
    response = viewer_client.get(path)
    assert response.status_code == 403


@pytest.mark.parametrize('path', PROTECTED_ADMIN_ROUTES_POST)
def test_viewer_gets_403_on_admin_mutations(viewer_client, path):
    response = viewer_client.post(path, data={'name': 'Sneaky Bar', 'tables': '2'})
    assert response.status_code == 403


def test_viewer_can_still_see_public_seasons_list(viewer_client):
    response = viewer_client.get('/seasons')
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Login / logout flow
# ---------------------------------------------------------------------------

def test_login_with_valid_credentials_redirects_to_seasons(app):
    create_user(app, ADMIN_USERNAME, ADMIN_PASSWORD, role='admin')
    client = app.test_client()
    response = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    assert response.status_code == 302
    assert '/seasons' in response.headers['Location']


def test_login_grants_access_to_admin_page(admin_client):
    response = admin_client.get('/admin')
    assert response.status_code == 200


def test_login_with_wrong_password_shows_generic_error(app):
    create_user(app, ADMIN_USERNAME, ADMIN_PASSWORD, role='admin')
    client = app.test_client()
    response = login(client, ADMIN_USERNAME, 'not-the-password')
    assert response.status_code == 200  # re-renders the form, no redirect
    assert b'Invalid username or password.' in response.data


def test_login_with_unknown_user_shows_same_generic_error(app):
    client = app.test_client()
    response = login(client, 'nobody', 'irrelevant')
    assert response.status_code == 200
    assert b'Invalid username or password.' in response.data


def test_logout_ends_the_session(admin_client):
    response = admin_client.get('/logout')
    assert response.status_code == 302
    assert '/login' in response.headers['Location']

    # The same client must now be treated as anonymous again.
    response = admin_client.get('/admin')
    assert response.status_code == 302
    assert '/login' in response.headers['Location']


def test_login_next_param_ignores_external_urls(app):
    create_user(app, ADMIN_USERNAME, ADMIN_PASSWORD, role='admin')
    client = app.test_client()
    response = client.post('/login?next=https://evil.example.com/phish',
                           data={'username': ADMIN_USERNAME,
                                 'password': ADMIN_PASSWORD})
    assert response.status_code == 302
    assert 'evil.example.com' not in response.headers['Location']
