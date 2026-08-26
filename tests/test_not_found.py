"""
tests/test_not_found.py — 404 coverage for routes converted from legacy
SQLAlchemy lookups (Model.query.get / Model.query.get_or_404) to
db.session.get / db.get_or_404 in app/main/routes.py.

The refactor's whole point is behavior-preserving: an id that doesn't exist
must still produce a 404, not a 500 (db.session.get returns None instead of
raising, so a naive swap silently changes behavior — e.g. calling
.season_id on None). Every one of the 27 converted call sites gets exercised
here with a nonexistent id.
"""

import pytest
from datetime import date

from app import db
from app.models import Season


NONEXISTENT_ID = 999999


@pytest.fixture
def existing_season_id(app, sample_league):
    """An id of a real, persisted Season — for tests that need one id valid
    and a second id nonexistent (e.g. blackout_delete's two lookups)."""
    with app.app_context():
        season = Season(name='Existing Season', start_date=date(2026, 9, 1))
        db.session.add(season)
        db.session.commit()
        return season.id


# ---------------------------------------------------------------------------
# Public season routes — db.get_or_404(Season, season_id), no login required
# ---------------------------------------------------------------------------

PUBLIC_SEASON_GET_ROUTES = [
    '/seasons/{0}',
    '/seasons/{0}/print',
    '/seasons/{0}/export',
    '/seasons/{0}/export/csv',
    '/seasons/{0}/compact',
]


@pytest.mark.parametrize('template', PUBLIC_SEASON_GET_ROUTES)
def test_public_season_route_404s_for_nonexistent_season(client, template):
    response = client.get(template.format(NONEXISTENT_ID))
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Admin-protected season POST routes keyed on season_id alone
# ---------------------------------------------------------------------------

ADMIN_SEASON_POST_ROUTES = [
    '/seasons/{0}/regenerate',
    '/seasons/{0}/regenerate-partial',
    '/seasons/{0}/blackouts/add',
    '/seasons/{0}/archive',
]


@pytest.mark.parametrize('template', ADMIN_SEASON_POST_ROUTES)
def test_admin_season_post_route_404s_for_nonexistent_season(admin_client, template):
    response = admin_client.post(template.format(NONEXISTENT_ID), data={})
    assert response.status_code == 404


def test_blackout_delete_404s_for_nonexistent_season(admin_client):
    response = admin_client.post(
        '/seasons/{0}/blackouts/{1}/delete'.format(NONEXISTENT_ID, 888888), data={})
    assert response.status_code == 404


def test_blackout_delete_404s_for_nonexistent_blackout(admin_client, existing_season_id):
    response = admin_client.post(
        '/seasons/{0}/blackouts/{1}/delete'.format(existing_season_id, NONEXISTENT_ID),
        data={})
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Admin CRUD routes keyed on a single nonexistent id
# ---------------------------------------------------------------------------

ADMIN_SINGLE_ID_POST_ROUTES = [
    '/admin/bars/{0}/edit',
    '/admin/bars/{0}/delete',
    '/admin/teams/{0}/edit',
    '/admin/teams/{0}/delete',
    '/admin/teams/{0}/players/add',
    '/admin/users/{0}/edit',
    '/admin/users/{0}/delete',
    '/admin/league-types/{0}/edit',
    '/admin/league-types/{0}/delete',
    '/admin/players/{0}/edit',
    '/admin/players/{0}/delete',
]


@pytest.mark.parametrize('template', ADMIN_SINGLE_ID_POST_ROUTES)
def test_admin_single_id_route_404s_for_nonexistent_id(admin_client, template):
    response = admin_client.post(template.format(NONEXISTENT_ID), data={})
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# player_assign / player_unassign — two lookups apiece (team, then player)
# ---------------------------------------------------------------------------

def test_player_assign_404s_for_nonexistent_team(admin_client):
    response = admin_client.post(
        '/admin/teams/{0}/players/assign'.format(NONEXISTENT_ID),
        data={'player_id': '1'})
    assert response.status_code == 404


def test_player_assign_404s_for_nonexistent_player(admin_client, sample_league):
    team_id = sample_league['team_ids'][0]
    response = admin_client.post(
        '/admin/teams/{0}/players/assign'.format(team_id),
        data={'player_id': str(NONEXISTENT_ID)})
    assert response.status_code == 404


def test_player_unassign_404s_for_nonexistent_team(admin_client):
    response = admin_client.post(
        '/admin/teams/{0}/players/{1}/unassign'.format(NONEXISTENT_ID, 1), data={})
    assert response.status_code == 404


def test_player_unassign_404s_for_nonexistent_player(admin_client, sample_league):
    team_id = sample_league['team_ids'][0]
    response = admin_client.post(
        '/admin/teams/{0}/players/{1}/unassign'.format(team_id, NONEXISTENT_ID), data={})
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# db.session.get(LeagueType, ...) — the one non-get_or_404 conversion.
# Invoked indirectly through season_new's validation, which treats "not
# found" as a form error rather than a 404 (the id came from a select
# dropdown, so an invalid value is user input, not a routing miss).
# ---------------------------------------------------------------------------

def test_season_new_rejects_unknown_league_type_id(admin_client, sample_league):
    form = {
        'name': 'Bad League Type Season',
        'start_date': '2026-09-01',
        'frequency': 'weekly',
        'length_mode': 'num_weeks',
        'num_weeks': '3',
        'team_ids': [str(tid) for tid in sample_league['team_ids']],
        'league_type_id': str(NONEXISTENT_ID),
    }
    response = admin_client.post('/seasons/new', data=form)
    assert response.status_code == 200  # form re-rendered with errors
    assert b'Invalid league type.' in response.data

    with admin_client.application.app_context():
        assert Season.query.filter_by(name='Bad League Type Season').first() is None
