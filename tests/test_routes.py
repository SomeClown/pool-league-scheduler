"""
tests/test_routes.py — Core route behavior: seasons list, season creation
happy path, and the PWA root-scope routes.
"""

from datetime import date

from app import db
from app.models import Bye, Match, Season, SeasonBarCap


# ---------------------------------------------------------------------------
# Seasons list
# ---------------------------------------------------------------------------

def test_index_redirects_to_seasons(client):
    response = client.get('/')
    assert response.status_code == 302
    assert '/seasons' in response.headers['Location']


def test_seasons_list_renders_for_anonymous_users(client):
    response = client.get('/seasons')
    assert response.status_code == 200


def test_seasons_list_shows_existing_season(app, client):
    with app.app_context():
        db.session.add(Season(name='Fall Classic 2026', start_date=date(2026, 9, 1)))
        db.session.commit()
    response = client.get('/seasons')
    assert response.status_code == 200
    assert b'Fall Classic 2026' in response.data


# ---------------------------------------------------------------------------
# Season creation (admin happy path)
# ---------------------------------------------------------------------------

def test_season_new_form_renders_for_admin(admin_client, sample_league):
    response = admin_client.get('/seasons/new')
    assert response.status_code == 200


def test_season_creation_happy_path(app, admin_client, sample_league):
    # 4 teams, weekly, 3 weeks → exactly one round-robin cycle:
    # 3 rounds × 2 matches, no byes.
    form = {
        'name': 'Winter League 2026',
        'start_date': '2026-09-01',
        'frequency': 'weekly',
        'length_mode': 'num_weeks',
        'num_weeks': '3',
        'team_ids': [str(tid) for tid in sample_league['team_ids']],
    }
    response = admin_client.post('/seasons/new', data=form)
    assert response.status_code == 302, 'expected redirect to the new season detail page'

    with app.app_context():
        season = Season.query.filter_by(name='Winter League 2026').first()
        assert season is not None
        assert season.start_date == date(2026, 9, 1)
        assert season.status == 'active'

        matches = Match.query.filter_by(season_id=season.id).all()
        assert len(matches) == 6  # 3 rounds x 2 matches
        assert sorted({m.round_num for m in matches}) == [1, 2, 3]
        assert Bye.query.filter_by(season_id=season.id).count() == 0

        # A cap row is stored for every bar in the season, clamped to the
        # bar's standing limit when the form omits explicit values.
        caps = SeasonBarCap.query.filter_by(season_id=season.id).all()
        assert sorted(c.bar_id for c in caps) == sorted(sample_league['bar_ids'])
        for cap in caps:
            assert cap.tables_used == 2

        # Every match is hosted at the home team's bar.
        for match in matches:
            assert match.bar_id == match.home_team.bar_id

        season_id = season.id

    detail = admin_client.get('/seasons/{0}'.format(season_id))
    assert detail.status_code == 200
    assert b'Winter League 2026' in detail.data


def test_season_creation_rejects_fewer_than_two_teams(app, admin_client, sample_league):
    form = {
        'name': 'Lonely Season',
        'start_date': '2026-09-01',
        'frequency': 'weekly',
        'length_mode': 'num_weeks',
        'num_weeks': '3',
        'team_ids': [str(sample_league['team_ids'][0])],
    }
    response = admin_client.post('/seasons/new', data=form)
    assert response.status_code == 200  # form re-rendered with errors
    with app.app_context():
        assert Season.query.filter_by(name='Lonely Season').first() is None


# ---------------------------------------------------------------------------
# PWA routes (must be served from root scope, not /static)
# ---------------------------------------------------------------------------

def test_service_worker_served_from_root_with_correct_type(client):
    response = client.get('/sw.js')
    assert response.status_code == 200
    assert response.content_type.startswith('application/javascript')
    assert response.headers.get('Cache-Control') == 'no-cache'


def test_manifest_served_from_root_with_correct_type(client):
    response = client.get('/manifest.json')
    assert response.status_code == 200
    assert response.content_type.startswith('application/manifest+json')
