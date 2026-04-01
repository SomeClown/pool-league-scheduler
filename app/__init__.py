import click
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from config import Config

db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = 'auth.login'
login_manager.login_message = 'Please log in to access this page.'
login_manager.login_message_category = 'warning'


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    db.init_app(app)
    login_manager.init_app(app)

    from app.auth import bp as auth_bp
    app.register_blueprint(auth_bp)

    from app.main import bp as main_bp
    app.register_blueprint(main_bp)

    with app.app_context():
        db.create_all()

    _register_cli(app)

    return app


def _register_cli(app):
    @app.cli.command('create-admin')
    @click.argument('username')
    @click.argument('password')
    def create_admin(username, password):
        """Create an admin user.  Usage: flask create-admin <username> <password>"""
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
        """Grant superuser status to an existing user.  Usage: flask make-superuser <username>"""
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
        """Add new columns to existing database tables without losing data."""
        from sqlalchemy import text
        migrations = [
            ("seasons", "end_date", "ALTER TABLE seasons ADD COLUMN end_date DATE"),
            ("users", "is_superuser", "ALTER TABLE users ADD COLUMN is_superuser BOOLEAN NOT NULL DEFAULT 0"),
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
