from datetime import datetime
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from app import db, login_manager


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='viewer')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_admin(self):
        return self.role == 'admin'

    def __repr__(self):
        return f'<User {self.username}>'


class Bar(db.Model):
    __tablename__ = 'bars'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    # Number of pool tables — determines how many simultaneous matches can be hosted
    tables = db.Column(db.Integer, nullable=False, default=1)
    teams = db.relationship('Team', backref='bar', lazy=True)

    def __repr__(self):
        return f'<Bar {self.name}>'


class Team(db.Model):
    __tablename__ = 'teams'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    bar_id = db.Column(db.Integer, db.ForeignKey('bars.id'), nullable=False)

    def __repr__(self):
        return f'<Team {self.name}>'


# Many-to-many: a season includes multiple teams; a team can appear in multiple seasons
season_teams = db.Table(
    'season_teams',
    db.Column('season_id', db.Integer, db.ForeignKey('seasons.id'), primary_key=True),
    db.Column('team_id', db.Integer, db.ForeignKey('teams.id'), primary_key=True),
)


class Season(db.Model):
    __tablename__ = 'seasons'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    frequency = db.Column(db.String(20), nullable=False, default='weekly')
    status = db.Column(db.String(20), nullable=False, default='active')  # 'active' | 'archived'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    teams = db.relationship('Team', secondary=season_teams, backref='seasons', lazy=True)
    blackout_dates = db.relationship(
        'BlackoutDate', backref='season', lazy=True, cascade='all, delete-orphan'
    )
    matches = db.relationship(
        'Match', backref='season', lazy=True, cascade='all, delete-orphan'
    )
    byes = db.relationship(
        'Bye', backref='season', lazy=True, cascade='all, delete-orphan'
    )

    def __repr__(self):
        return f'<Season {self.name}>'


class BlackoutDate(db.Model):
    __tablename__ = 'blackout_dates'
    id = db.Column(db.Integer, primary_key=True)
    season_id = db.Column(db.Integer, db.ForeignKey('seasons.id'), nullable=False)
    date = db.Column(db.Date, nullable=False)


class Match(db.Model):
    __tablename__ = 'matches'
    id = db.Column(db.Integer, primary_key=True)
    season_id = db.Column(db.Integer, db.ForeignKey('seasons.id'), nullable=False)
    round_num = db.Column(db.Integer, nullable=False)
    date = db.Column(db.Date, nullable=False)
    home_team_id = db.Column(db.Integer, db.ForeignKey('teams.id'), nullable=False)
    away_team_id = db.Column(db.Integer, db.ForeignKey('teams.id'), nullable=False)
    bar_id = db.Column(db.Integer, db.ForeignKey('bars.id'), nullable=False)

    home_team = db.relationship('Team', foreign_keys=[home_team_id])
    away_team = db.relationship('Team', foreign_keys=[away_team_id])
    bar = db.relationship('Bar')


class Bye(db.Model):
    __tablename__ = 'byes'
    id = db.Column(db.Integer, primary_key=True)
    season_id = db.Column(db.Integer, db.ForeignKey('seasons.id'), nullable=False)
    round_num = db.Column(db.Integer, nullable=False)
    date = db.Column(db.Date, nullable=False)
    team_id = db.Column(db.Integer, db.ForeignKey('teams.id'), nullable=False)

    team = db.relationship('Team')
