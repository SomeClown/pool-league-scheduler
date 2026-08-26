"""
tests/test_admin_crud.py — admin CRUD guards: bar/team delete guards, user
management (role-escalation and superuser-only rules), clear-schedules, and
change-password.
"""

from app import db
from app.models import Bar, Bye, Match, Season, Team, User
from tests.conftest import ADMIN_PASSWORD, ADMIN_USERNAME, SUPERUSER_USERNAME, login


# ---------------------------------------------------------------------------
# Bar delete guard
# ---------------------------------------------------------------------------

def test_bar_delete_blocked_when_bar_has_teams(app, admin_client, sample_league):
    bar_id = sample_league['bar_ids'][0]
    response = admin_client.post(
        '/admin/bars/{0}/delete'.format(bar_id), data={}, follow_redirects=True)
    assert response.status_code == 200
    assert b'remove its teams first' in response.data
    with app.app_context():
        assert db.session.get(Bar, bar_id) is not None


def test_bar_delete_succeeds_when_bar_has_no_teams(app, admin_client):
    with app.app_context():
        bar = Bar(name='Empty Bar', tables=2, tables_in_use=2)
        db.session.add(bar)
        db.session.commit()
        bar_id = bar.id

    response = admin_client.post('/admin/bars/{0}/delete'.format(bar_id), data={})
    assert response.status_code == 302
    with app.app_context():
        assert db.session.get(Bar, bar_id) is None


# ---------------------------------------------------------------------------
# Team delete guard
# ---------------------------------------------------------------------------

def test_team_delete_blocked_when_team_belongs_to_a_season(
        app, admin_client, create_season, sample_league):
    create_season('Team Guard Season', sample_league['team_ids'], num_weeks=3)
    team_id = sample_league['team_ids'][0]

    response = admin_client.post(
        '/admin/teams/{0}/delete'.format(team_id), data={}, follow_redirects=True)
    assert response.status_code == 200
    assert b'it belongs to one or more seasons' in response.data
    with app.app_context():
        assert db.session.get(Team, team_id) is not None


def test_team_delete_succeeds_when_team_has_no_seasons(app, admin_client, sample_league):
    team_id = sample_league['team_ids'][0]
    response = admin_client.post('/admin/teams/{0}/delete'.format(team_id), data={})
    assert response.status_code == 302
    with app.app_context():
        assert db.session.get(Team, team_id) is None


# ---------------------------------------------------------------------------
# User management — role escalation and superuser-only rules
# ---------------------------------------------------------------------------

def test_regular_admin_cannot_create_admin_account(app, admin_client):
    response = admin_client.post('/admin/users/add', data={
        'username': 'sneaky-admin', 'password': 'password123', 'role': 'admin',
    }, follow_redirects=True)
    assert response.status_code == 200
    assert b'Only the superuser can create admin accounts.' in response.data
    with app.app_context():
        assert User.query.filter_by(username='sneaky-admin').first() is None


def test_superuser_can_create_admin_account(app, superuser_client):
    response = superuser_client.post('/admin/users/add', data={
        'username': 'new-admin', 'password': 'password123', 'role': 'admin',
    })
    assert response.status_code == 302
    with app.app_context():
        user = User.query.filter_by(username='new-admin').first()
        assert user is not None
        assert user.role == 'admin'


def test_regular_admin_cannot_edit_another_admin_account(app, admin_client):
    with app.app_context():
        other_admin = User(username='other-admin', role='admin')
        other_admin.set_password('irrelevant-pw-1')
        db.session.add(other_admin)
        db.session.commit()
        other_admin_id = other_admin.id

    response = admin_client.post(
        '/admin/users/{0}/edit'.format(other_admin_id),
        data={'username': 'renamed', 'role': 'admin'}, follow_redirects=True)
    assert response.status_code == 200
    assert b'Only the superuser can edit admin accounts.' in response.data
    with app.app_context():
        assert db.session.get(User, other_admin_id).username == 'other-admin'


def test_regular_admin_cannot_escalate_a_viewer_to_admin(app, admin_client):
    with app.app_context():
        viewer = User(username='plain-viewer', role='viewer')
        viewer.set_password('irrelevant-pw-1')
        db.session.add(viewer)
        db.session.commit()
        viewer_id = viewer.id

    response = admin_client.post(
        '/admin/users/{0}/edit'.format(viewer_id),
        data={'username': 'plain-viewer', 'role': 'admin'})
    assert response.status_code == 302  # silently reverted, not a rejection
    with app.app_context():
        assert db.session.get(User, viewer_id).role == 'viewer'


def test_superuser_can_promote_a_viewer_to_admin(app, superuser_client):
    with app.app_context():
        viewer = User(username='promotable-viewer', role='viewer')
        viewer.set_password('irrelevant-pw-1')
        db.session.add(viewer)
        db.session.commit()
        viewer_id = viewer.id

    response = superuser_client.post(
        '/admin/users/{0}/edit'.format(viewer_id),
        data={'username': 'promotable-viewer', 'role': 'admin'})
    assert response.status_code == 302
    with app.app_context():
        assert db.session.get(User, viewer_id).role == 'admin'


def test_user_cannot_delete_their_own_account(app, admin_client):
    with app.app_context():
        self_id = User.query.filter_by(username=ADMIN_USERNAME).first().id

    response = admin_client.post(
        '/admin/users/{0}/delete'.format(self_id), data={}, follow_redirects=True)
    assert response.status_code == 200
    assert b'You cannot delete your own account.' in response.data
    with app.app_context():
        assert db.session.get(User, self_id) is not None


def test_superuser_account_cannot_be_deleted_through_the_ui(app, admin_client, superuser_client):
    with app.app_context():
        superuser_id = User.query.filter_by(username=SUPERUSER_USERNAME).first().id

    response = admin_client.post(
        '/admin/users/{0}/delete'.format(superuser_id), data={}, follow_redirects=True)
    assert response.status_code == 200
    assert b'The superuser account cannot be deleted.' in response.data
    with app.app_context():
        assert db.session.get(User, superuser_id) is not None


def test_regular_admin_cannot_delete_another_admin_account(app, admin_client):
    with app.app_context():
        other_admin = User(username='doomed-admin', role='admin')
        other_admin.set_password('irrelevant-pw-1')
        db.session.add(other_admin)
        db.session.commit()
        other_admin_id = other_admin.id

    response = admin_client.post(
        '/admin/users/{0}/delete'.format(other_admin_id), data={}, follow_redirects=True)
    assert response.status_code == 200
    assert b'Only the superuser can delete admin accounts.' in response.data
    with app.app_context():
        assert db.session.get(User, other_admin_id) is not None


# ---------------------------------------------------------------------------
# clear-schedules — superuser only, wipes schedule data but not bars/teams
# ---------------------------------------------------------------------------

def test_clear_schedules_requires_superuser_not_just_admin(
        app, admin_client, create_season, sample_league):
    create_season('To Be Cleared', sample_league['team_ids'], num_weeks=3)

    response = admin_client.post('/admin/clear-schedules', data={})
    assert response.status_code == 403
    with app.app_context():
        assert Season.query.count() == 1


def test_clear_schedules_wipes_seasons_but_keeps_bars_and_teams(
        app, superuser_client, create_season, sample_league):
    create_season('To Be Cleared', sample_league['team_ids'], num_weeks=3)

    response = superuser_client.post('/admin/clear-schedules', data={})
    assert response.status_code == 302

    with app.app_context():
        assert Season.query.count() == 0
        assert Match.query.count() == 0
        assert Bye.query.count() == 0
        assert Bar.query.count() == 2
        assert Team.query.count() == 4


# ---------------------------------------------------------------------------
# change-password
# ---------------------------------------------------------------------------

def test_change_password_rejects_wrong_current_password(app, admin_client):
    response = admin_client.post('/account/password', data={
        'current_password': 'totally-wrong-pw',
        'new_password': 'newpassword123',
        'confirm_password': 'newpassword123',
    }, follow_redirects=True)
    assert response.status_code == 200
    assert b'Current password is incorrect.' in response.data

    # The original password must still work.
    fresh_client = app.test_client()
    login_response = login(fresh_client, ADMIN_USERNAME, ADMIN_PASSWORD)
    assert login_response.status_code == 302


def test_change_password_rejects_short_new_password(admin_client):
    response = admin_client.post('/account/password', data={
        'current_password': ADMIN_PASSWORD,
        'new_password': 'short',
        'confirm_password': 'short',
    }, follow_redirects=True)
    assert response.status_code == 200
    assert b'New password must be at least 8 characters.' in response.data


def test_change_password_rejects_mismatched_confirmation(admin_client):
    response = admin_client.post('/account/password', data={
        'current_password': ADMIN_PASSWORD,
        'new_password': 'newpassword123',
        'confirm_password': 'different-confirm-1',
    }, follow_redirects=True)
    assert response.status_code == 200
    assert b'New passwords do not match.' in response.data


def test_change_password_success_updates_credentials(app, admin_client):
    response = admin_client.post('/account/password', data={
        'current_password': ADMIN_PASSWORD,
        'new_password': 'brandnewpassword123',
        'confirm_password': 'brandnewpassword123',
    })
    assert response.status_code == 302

    old_password_client = app.test_client()
    old_login = login(old_password_client, ADMIN_USERNAME, ADMIN_PASSWORD)
    assert old_login.status_code == 200  # rejected, form re-rendered

    new_password_client = app.test_client()
    new_login = login(new_password_client, ADMIN_USERNAME, 'brandnewpassword123')
    assert new_login.status_code == 302
