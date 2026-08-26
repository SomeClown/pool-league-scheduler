"""
tests/test_blackouts.py — blackout_add / blackout_delete happy paths and
_remap_dates (exercised indirectly through those routes).
"""

from datetime import date

from app.models import BlackoutDate, Match


def _round_dates(app, season_id):
    """{round_num: date} for every round of a season."""
    with app.app_context():
        matches = Match.query.filter_by(season_id=season_id).all()
        by_round = {}
        for m in matches:
            by_round.setdefault(m.round_num, m.date)
        return by_round


def test_adding_a_blackout_shifts_that_and_later_rounds_forward(
        app, admin_client, create_season, sample_league):
    season_id = create_season('Blackout Season', sample_league['team_ids'],
                              start_date='2026-09-01', num_weeks=4)
    before = _round_dates(app, season_id)
    assert before == {
        1: date(2026, 9, 1),
        2: date(2026, 9, 8),
        3: date(2026, 9, 15),
        4: date(2026, 9, 22),
    }

    # Black out what is currently round 2's date.
    response = admin_client.post(
        '/seasons/{0}/blackouts/add'.format(season_id), data={'date': '2026-09-08'})
    assert response.status_code == 302

    after = _round_dates(app, season_id)
    assert after[1] == date(2026, 9, 1), 'round before the blackout must stay put'
    assert after[2] == date(2026, 9, 15), 'round on the blackout hops to the next weekly slot'
    # _remap_dates walks the whole season sequentially, so every later round
    # shifts forward by one step too, preserving the weekly cadence.
    assert after[3] == date(2026, 9, 22)
    assert after[4] == date(2026, 9, 29)
    assert date(2026, 9, 8) not in after.values()


def test_deleting_a_blackout_restores_original_dates(
        app, admin_client, create_season, sample_league):
    season_id = create_season('Blackout Restore Season', sample_league['team_ids'],
                              start_date='2026-09-01', num_weeks=4)
    before = _round_dates(app, season_id)

    add_response = admin_client.post(
        '/seasons/{0}/blackouts/add'.format(season_id), data={'date': '2026-09-08'})
    assert add_response.status_code == 302
    assert _round_dates(app, season_id) != before  # sanity: the add actually changed things

    with app.app_context():
        blackout_id = BlackoutDate.query.filter_by(season_id=season_id).first().id

    delete_response = admin_client.post(
        '/seasons/{0}/blackouts/{1}/delete'.format(season_id, blackout_id), data={})
    assert delete_response.status_code == 302

    after = _round_dates(app, season_id)
    assert after == before
    with app.app_context():
        assert BlackoutDate.query.filter_by(season_id=season_id).count() == 0


def test_blackout_add_rejects_duplicate_date(app, admin_client, create_season, sample_league):
    season_id = create_season('Dup Blackout Season', sample_league['team_ids'],
                              start_date='2026-09-01', num_weeks=4)
    admin_client.post('/seasons/{0}/blackouts/add'.format(season_id), data={'date': '2026-09-08'})

    response = admin_client.post(
        '/seasons/{0}/blackouts/add'.format(season_id),
        data={'date': '2026-09-08'}, follow_redirects=True)
    assert response.status_code == 200
    assert b'That date is already a blackout date.' in response.data
    with app.app_context():
        assert BlackoutDate.query.filter_by(season_id=season_id).count() == 1


def test_blackout_add_rejects_invalid_date_format(app, admin_client, create_season, sample_league):
    season_id = create_season('Invalid Blackout Season', sample_league['team_ids'],
                              start_date='2026-09-01', num_weeks=4)

    response = admin_client.post(
        '/seasons/{0}/blackouts/add'.format(season_id),
        data={'date': 'not-a-date'}, follow_redirects=True)
    assert response.status_code == 200
    assert b'Invalid date. Please use the date picker.' in response.data
    with app.app_context():
        assert BlackoutDate.query.filter_by(season_id=season_id).count() == 0


def test_blackout_management_blocked_on_archived_season(
        app, admin_client, create_season, sample_league):
    season_id = create_season('Archived Blackout Season', sample_league['team_ids'],
                              start_date='2026-09-01', num_weeks=4)
    admin_client.post('/seasons/{0}/archive'.format(season_id), data={})

    response = admin_client.post(
        '/seasons/{0}/blackouts/add'.format(season_id),
        data={'date': '2026-09-08'}, follow_redirects=True)
    assert response.status_code == 200
    assert b'Blackout dates can only be modified on active seasons.' in response.data
    with app.app_context():
        assert BlackoutDate.query.filter_by(season_id=season_id).count() == 0
