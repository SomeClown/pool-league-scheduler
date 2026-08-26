"""
app/__init__.py — Application factory and CLI command registration.

Uses the Flask application factory pattern so the app instance can be created
with different configurations (useful for testing, though let's be honest —
we both know you're not writing tests). Blueprints are registered here, the
database is initialized, and CLI commands are wired up.

Important: CLI commands MUST be registered inside create_app() via
_register_cli(). If you put them in run.py or anywhere else, Flask's factory
discovery won't find them and 'flask your-command' will return a very unhelpful
"No such command" error. Ask me how I know.
"""

import click
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from config import Config

# Module-level extensions — initialized without an app instance here,
# then properly bound inside create_app() via init_app(). This is the
# standard Flask pattern. It's not complicated, but it does trip people up.
db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = 'auth.login'
login_manager.login_message = 'Please log in to access this page.'
login_manager.login_message_category = 'warning'


def create_app(config_class=Config):
    """
    Application factory. Creates and configures a Flask app instance.

    Registers the auth and main blueprints, initializes the database
    (creating tables if they don't exist), and wires up CLI commands.
    Returns the fully configured app.

    Args:
        config_class: A config object to load settings from. Defaults to
                      the production Config. Pass a test config if you're
                      one of those people who writes tests.
    """
    app = Flask(__name__)
    app.config.from_object(config_class)

    db.init_app(app)
    login_manager.init_app(app)

    # Register blueprints — auth handles login/logout, main handles everything else.
    from app.auth import bp as auth_bp
    app.register_blueprint(auth_bp)

    from app.main import bp as main_bp
    app.register_blueprint(main_bp)

    with app.app_context():
        # Create any tables that don't already exist. Does NOT modify existing
        # tables — for column additions, use 'flask db-migrate' instead.
        db.create_all()

    _register_cli(app)

    return app


def _register_cli(app):
    """
    Register all Flask CLI management commands with the app instance.

    Commands are defined here (rather than in run.py) so they're available
    regardless of how Flask discovers the app. If a command isn't showing up
    in 'flask --help', this is the first place to look.
    """

    @app.cli.command('create-admin')
    @click.argument('username')
    @click.argument('password')
    def create_admin(username, password):
        """
        Create a new admin user from the command line.

        Usage: flask create-admin <username> <password>

        Creates the user with role='admin'. To grant full superuser privileges
        (including the ability to delete other admins), run make-superuser
        afterward. Will refuse to create a duplicate username.
        """
        from app.models import User
        with app.app_context():
            if User.query.filter_by(username=username).first():
                click.echo(f'User "{username}" already exists.')
                return
            user = User(username=username, role='admin')
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            click.echo(f'Admin user "{username}" created successfully.')

    @app.cli.command('make-superuser')
    @click.argument('username')
    def make_superuser(username):
        """
        Promote an existing user to superuser status.

        Usage: flask make-superuser <username>

        Superusers can do everything admins can do, plus delete or demote
        other admin accounts. There should really only be one of these.
        With great power comes great responsibility, or so I'm told.
        """
        from app.models import User
        with app.app_context():
            user = User.query.filter_by(username=username).first()
            if not user:
                click.echo(f'User "{username}" not found.')
                return
            user.is_superuser = True
            user.role = 'admin'
            db.session.commit()
            click.echo(f'"{username}" is now a superuser.')

    @app.cli.command('db-migrate')
    def db_migrate():
        """
        Add new columns to existing database tables without destroying data.

        Usage: flask db-migrate

        SQLAlchemy's create_all() creates missing tables but won't modify
        existing ones. When a new column is added to a model, run this command
        to apply the ALTER TABLE statements. Already-existing columns are
        skipped safely. Think of it as a very minimal, very manual Alembic.
        """
        from sqlalchemy import text
        # Each entry is (table_name, column_name, sql_to_run).
        # Add new migrations to this list — don't remove old ones, or the
        # skip logic won't protect you when running on a fresh database.
        migrations = [
            ("seasons", "end_date",     "ALTER TABLE seasons ADD COLUMN end_date DATE"),
            ("users",   "is_superuser", "ALTER TABLE users ADD COLUMN is_superuser BOOLEAN NOT NULL DEFAULT 0"),
            ("teams",   "number",       "ALTER TABLE teams ADD COLUMN number INTEGER"),
            ("bars",    "tables_in_use", "ALTER TABLE bars ADD COLUMN tables_in_use INTEGER"),
            # Backfill: existing bars schedule on all their tables. Idempotent
            # (WHERE IS NULL), so re-running this migration is harmless.
            ("bars",    "tables_in_use (backfill)",
             "UPDATE bars SET tables_in_use = tables WHERE tables_in_use IS NULL"),
            ("seasons", "league_type_id",
             "ALTER TABLE seasons ADD COLUMN league_type_id INTEGER REFERENCES league_types(id)"),
        ]
        with app.app_context():
            with db.engine.connect() as conn:
                for table, column, sql in migrations:
                    try:
                        conn.execute(text(sql))
                        conn.commit()
                        click.echo(f'Added column "{column}" to "{table}".')
                    except Exception:
                        click.echo(f'Column "{column}" in "{table}" already exists — skipping.')

    @app.cli.command('migrate-f16')
    def migrate_f16():
        """
        Migrate Player model from per-team to master database with many-to-many assignment.

        Usage: flask migrate-f16

        Creates the player_teams join table, migrates existing team_id assignments
        into it, then rebuilds the players table without the team_id column. Safe
        to re-run: checks whether team_id still exists before doing the rebuild.
        """
        from sqlalchemy import text
        with app.app_context():
            with db.engine.connect() as conn:
                # Step 1: create player_teams join table
                conn.execute(text(
                    "CREATE TABLE IF NOT EXISTS player_teams ("
                    "  player_id INTEGER NOT NULL REFERENCES players(id),"
                    "  team_id   INTEGER NOT NULL REFERENCES teams(id),"
                    "  PRIMARY KEY (player_id, team_id)"
                    ")"
                ))
                conn.commit()
                click.echo('player_teams table ready.')

                # Step 2: check whether players.team_id still exists
                cols = [row[1] for row in conn.execute(text('PRAGMA table_info(players)'))]
                if 'team_id' not in cols:
                    click.echo('players.team_id already removed — nothing to do.')
                    return

                # Step 3: copy existing assignments into join table
                conn.execute(text(
                    "INSERT OR IGNORE INTO player_teams (player_id, team_id) "
                    "SELECT id, team_id FROM players WHERE team_id IS NOT NULL"
                ))
                conn.commit()
                click.echo('Existing player-team assignments migrated.')

                # Step 4: rebuild players table without team_id column (SQLite
                # cannot drop a column in place; table-rebuild is the safe path).
                conn.execute(text(
                    "CREATE TABLE players_new ("
                    "  id    INTEGER PRIMARY KEY,"
                    "  name  VARCHAR(100) NOT NULL,"
                    "  email VARCHAR(200),"
                    "  phone VARCHAR(50)"
                    ")"
                ))
                conn.execute(text(
                    "INSERT INTO players_new (id, name, email, phone) "
                    "SELECT id, name, email, phone FROM players"
                ))
                conn.execute(text('DROP TABLE players'))
                conn.execute(text('ALTER TABLE players_new RENAME TO players'))
                conn.commit()
                click.echo('players table rebuilt (team_id column removed).')
                click.echo('F-16 migration complete.')

    @app.cli.command('seed-league-types')
    def seed_league_types():
        """
        Seed the initial league type rows into the league_types table.

        Usage: flask seed-league-types

        Inserts the four standard Snoqualmie Valley league types if they do not
        already exist. This command is idempotent — safe to re-run as many times
        as needed; it will never create duplicates or raise an error on rows that
        are already present.

        New league types added after the initial seed can be managed through the
        admin UI without running this command again.
        """
        from app.models import LeagueType
        names = [
            "Snoqualmie Valley Men's League",
            "Snoqualmie Valley Women's League",
            "Snoqualmie Valley Mixed Doubles League",
            "Snoqualmie Valley BCA",
        ]
        with app.app_context():
            added = 0
            for sort_order, name in enumerate(names, start=1):
                if LeagueType.query.filter_by(name=name).first():
                    click.echo(f'Already exists: "{name}"')
                else:
                    db.session.add(LeagueType(name=name, sort_order=sort_order))
                    click.echo(f'Seeded: "{name}"')
                    added += 1
            db.session.commit()
            click.echo(f'Done. {added} row(s) added.')
