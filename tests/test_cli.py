"""
tests/test_cli.py — Flask CLI commands (app/__init__.py::_register_cli),
invoked via app.test_cli_runner() against the per-test temp database.

db-migrate and migrate-f16 are only covered for what's hermetically
testable: idempotence when run against an already-current schema. Neither
command's actual "add a genuinely missing column / rebuild a genuinely
stale players table" path is exercised — doing that would require
constructing a deliberately outdated schema, which isn't a natural fit for
a per-test throwaway database built from the current models. See the final
report for this gap.
"""

from app import db
from app.models import LeagueType, User


# ---------------------------------------------------------------------------
# create-admin
# ---------------------------------------------------------------------------

def test_create_admin_creates_a_new_admin_user(app):
    runner = app.test_cli_runner()
    result = runner.invoke(args=['create-admin', 'clibob', 'clipassword1'])
    assert result.exit_code == 0
    assert 'created successfully' in result.output

    with app.app_context():
        user = User.query.filter_by(username='clibob').first()
        assert user is not None
        assert user.role == 'admin'
        assert user.check_password('clipassword1')


def test_create_admin_refuses_duplicate_username(app):
    runner = app.test_cli_runner()
    runner.invoke(args=['create-admin', 'clibob', 'clipassword1'])
    result = runner.invoke(args=['create-admin', 'clibob', 'a-different-pw'])
    assert result.exit_code == 0
    assert 'already exists' in result.output

    with app.app_context():
        assert User.query.filter_by(username='clibob').count() == 1


# ---------------------------------------------------------------------------
# make-superuser
# ---------------------------------------------------------------------------

def test_make_superuser_promotes_an_existing_user(app):
    runner = app.test_cli_runner()
    runner.invoke(args=['create-admin', 'clibob', 'clipassword1'])
    result = runner.invoke(args=['make-superuser', 'clibob'])
    assert result.exit_code == 0
    assert 'is now a superuser' in result.output

    with app.app_context():
        user = User.query.filter_by(username='clibob').first()
        assert user.is_superuser is True
        assert user.role == 'admin'


def test_make_superuser_reports_unknown_username(app):
    runner = app.test_cli_runner()
    result = runner.invoke(args=['make-superuser', 'nobody-registered'])
    assert result.exit_code == 0
    assert 'not found' in result.output


# ---------------------------------------------------------------------------
# seed-league-types
# ---------------------------------------------------------------------------

def test_seed_league_types_creates_the_four_standard_types(app):
    runner = app.test_cli_runner()
    result = runner.invoke(args=['seed-league-types'])
    assert result.exit_code == 0
    assert '4 row(s) added' in result.output

    with app.app_context():
        names = {lt.name for lt in LeagueType.query.all()}
        assert names == {
            "Snoqualmie Valley Men's League",
            "Snoqualmie Valley Women's League",
            "Snoqualmie Valley Mixed Doubles League",
            "Snoqualmie Valley BCA",
        }


def test_seed_league_types_is_idempotent(app):
    runner = app.test_cli_runner()
    runner.invoke(args=['seed-league-types'])
    result = runner.invoke(args=['seed-league-types'])
    assert result.exit_code == 0
    assert '0 row(s) added' in result.output

    with app.app_context():
        assert LeagueType.query.count() == 4


# ---------------------------------------------------------------------------
# db-migrate / migrate-f16 — idempotence only (see module docstring)
# ---------------------------------------------------------------------------

def test_db_migrate_is_idempotent_against_a_fresh_schema(app):
    # create_all() already builds every column current models.py defines,
    # so every ALTER TABLE in the migrations list should no-op ("already
    # exists — skipping") against a freshly created test database. This
    # only proves the command doesn't crash when run against an
    # already-current schema; it does not exercise the actual
    # missing-column code path.
    runner = app.test_cli_runner()
    first = runner.invoke(args=['db-migrate'])
    second = runner.invoke(args=['db-migrate'])
    assert first.exit_code == 0
    assert second.exit_code == 0
    assert 'already exists' in first.output


def test_migrate_f16_is_idempotent_against_a_fresh_schema(app):
    # Player.team_id was already removed from the current schema (see
    # models.py), so both invocations should take the "already removed"
    # early-exit path without error.
    runner = app.test_cli_runner()
    first = runner.invoke(args=['migrate-f16'])
    second = runner.invoke(args=['migrate-f16'])
    assert first.exit_code == 0
    assert second.exit_code == 0
    assert 'already removed' in first.output
    assert 'already removed' in second.output
