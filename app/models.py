"""
app/models.py — SQLAlchemy ORM models for the pool league scheduler.

Defines the full data model: users, bars, teams, seasons, matches, byes,
blackout dates, and per-season table cap overrides. All relationships are
defined here; the database schema is derived from these classes automatically
via db.create_all().

If you're adding a new column to an existing model, remember that create_all()
won't touch tables that already exist. Add the column to the migrations list
in app/__init__.py and run 'flask db-migrate'. Yes, every time. No, there's
no shortcut.
"""

from datetime import datetime
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from app import db, login_manager


@login_manager.user_loader
def load_user(user_id):
    """
    Required callback for Flask-Login. Reloads the user from the database
    using the ID stored in the session cookie.

    Returns None if the user doesn't exist (e.g. deleted after login),
    which will cause Flask-Login to treat the session as unauthenticated.
    """
    return db.session.get(User, int(user_id))


class User(UserMixin, db.Model):
    """
    Represents a league member who can log in to the application.

    Roles:
        viewer    — read-only access; can view and print schedules
        admin     — can manage bars, teams, seasons, and viewer accounts
        superuser — same as admin, plus can manage and delete admin accounts;
                    only one should exist (set via 'flask make-superuser')

    Passwords are stored as bcrypt hashes via Werkzeug. Plain-text passwords
    are never stored — if you find one, something has gone very wrong.
    """

    __tablename__ = 'users'

    id           = db.Column(db.Integer, primary_key=True)
    username     = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role         = db.Column(db.String(20), nullable=False, default='viewer')
    is_superuser = db.Column(db.Boolean, nullable=False, default=False)

    def set_password(self, password):
        """Hash and store a new password. Call this; never set password_hash directly."""
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        """Return True if the provided plain-text password matches the stored hash."""
        return check_password_hash(self.password_hash, password)

    @property
    def is_admin(self):
        """
        Return True if this user has admin-level access or higher.

        Superusers are always considered admins — they get everything admins
        get plus the ability to delete other admin accounts. Think of it as
        the difference between a regular roommate agreement and the Sheldon
        Cooper Relationship Agreement.
        """
        return self.role == 'admin' or self.is_superuser

    def __repr__(self):
        return f'<User {self.username}>'


class Bar(db.Model):
    """
    Represents a physical bar / venue that hosts pool league matches.

    Each bar has one or more pool tables, which determines how many matches
    can be scheduled there simultaneously. Every team must belong to exactly
    one home bar. Matches are always played at the home team's bar — away
    teams travel to their opponent's venue.

    A bar cannot be deleted while it still has teams assigned to it.
    """

    __tablename__ = 'bars'

    id     = db.Column(db.Integer, primary_key=True)
    name   = db.Column(db.String(100), nullable=False, unique=True)
    tables = db.Column(db.Integer, nullable=False, default=1)

    # All teams whose home venue is this bar.
    teams  = db.relationship('Team', backref='bar', lazy=True)

    def __repr__(self):
        return f'<Bar {self.name}>'


class Team(db.Model):
    """
    Represents a pool league team.

    Each team has an optional league number (used for compact schedule display),
    a name, and a home bar. A team can participate in multiple seasons over time.
    The home bar determines where that team's home matches are played.

    Teams cannot be deleted while they are enrolled in any season.
    """

    __tablename__ = 'teams'

    id     = db.Column(db.Integer, primary_key=True)
    number = db.Column(db.Integer, nullable=True)   # optional league number, e.g. 1–12
    name   = db.Column(db.String(100), nullable=False, unique=True)
    bar_id = db.Column(db.Integer, db.ForeignKey('bars.id'), nullable=False)

    @property
    def display_name(self):
        """
        Return a formatted display name combining number and team name.

        If a league number is assigned, returns '#N Team Name'.
        If no number is set, returns just the team name. Callers don't need
        to check — just use display_name everywhere and it does the right thing.
        """
        return f'#{self.number} {self.name}' if self.number is not None else self.name

    def __repr__(self):
        return f'<Team {self.name}>'


# Association table for the many-to-many relationship between seasons and teams.
# A season includes multiple teams; a team can appear in multiple seasons over
# the years. This is a plain SQLAlchemy Table (not a Model class) because we
# don't need to query it directly — SQLAlchemy manages it automatically via
# the Season.teams relationship. Important: bulk-deleting Season rows will NOT
# cascade through this table unless you use explicit SQL. See clear_all_schedules().
season_teams = db.Table(
    'season_teams',
    db.Column('season_id', db.Integer, db.ForeignKey('seasons.id'), primary_key=True),
    db.Column('team_id',   db.Integer, db.ForeignKey('teams.id'),   primary_key=True),
)


class Season(db.Model):
    """
    Represents a pool league season — a named, date-bounded collection of
    scheduled matches between a set of teams.

    A season progresses through three informal states:
        active    — schedule is live and can be regenerated
        archived  — read-only; preserved for historical reference

    The scheduler generates one round-robin cycle per 'cycle' of team matchups,
    repeating cycles if the season is long enough for teams to meet more than
    once. Home/away assignments alternate between cycles.

    Cascade behavior: deleting a Season will cascade to its matches, byes,
    blackout_dates, and bar_caps — but ONLY when deleting via the ORM
    (session.delete(season)). Bulk deletions via query.delete() bypass the
    ORM cascade. You've been warned. Again.
    """

    __tablename__ = 'seasons'

    id         = db.Column(db.Integer, primary_key=True)
    name       = db.Column(db.String(100), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date   = db.Column(db.Date, nullable=True)
    frequency  = db.Column(db.String(20), nullable=False, default='weekly')
    status     = db.Column(db.String(20), nullable=False, default='active')  # 'active' | 'archived'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    teams         = db.relationship('Team', secondary=season_teams, backref='seasons', lazy=True)
    blackout_dates = db.relationship('BlackoutDate', backref='season', lazy=True, cascade='all, delete-orphan')
    bar_caps      = db.relationship('SeasonBarCap',   backref='season', lazy=True, cascade='all, delete-orphan')
    matches       = db.relationship('Match',          backref='season', lazy=True, cascade='all, delete-orphan')
    byes          = db.relationship('Bye',            backref='season', lazy=True, cascade='all, delete-orphan')

    def __repr__(self):
        return f'<Season {self.name}>'


class SeasonBarCap(db.Model):
    """
    Per-season override for a bar's table count.

    Normally, a bar's 'tables' column determines how many simultaneous matches
    it can host. This model allows that limit to be reduced for a specific season
    without permanently modifying the bar record. Useful when, say, one of three
    tables is out of commission for the season.

    If no SeasonBarCap exists for a bar in a given season, the scheduler falls
    back to bar.tables.
    """

    __tablename__ = 'season_bar_caps'

    id          = db.Column(db.Integer, primary_key=True)
    season_id   = db.Column(db.Integer, db.ForeignKey('seasons.id'), nullable=False)
    bar_id      = db.Column(db.Integer, db.ForeignKey('bars.id'),    nullable=False)
    tables_used = db.Column(db.Integer, nullable=False)

    bar = db.relationship('Bar')


class BlackoutDate(db.Model):
    """
    A single date on which no matches are scheduled for a given season.

    The scheduler skips blackout dates when mapping rounds to calendar dates,
    so the total number of match nights is preserved — they just shift forward.
    Blackout dates are per-season, so a holiday that affects one season doesn't
    automatically carry over to the next.
    """

    __tablename__ = 'blackout_dates'

    id        = db.Column(db.Integer, primary_key=True)
    season_id = db.Column(db.Integer, db.ForeignKey('seasons.id'), nullable=False)
    date      = db.Column(db.Date, nullable=False)


class Match(db.Model):
    """
    A single scheduled match between a home team and an away team.

    Matches are always played at the home team's bar — bar_id should always
    equal home_team.bar_id. The away team travels to the home team's venue.

    Matches belong to a season and a round (round_num). All matches in the
    same round share the same date. The home/away assignment is determined by
    the scheduler algorithm and alternates between cycles when teams meet more
    than once in a season.

    Note: home_team and away_team both reference the Team table, so
    foreign_keys must be specified explicitly to avoid SQLAlchemy confusion.
    """

    __tablename__ = 'matches'

    id           = db.Column(db.Integer, primary_key=True)
    season_id    = db.Column(db.Integer, db.ForeignKey('seasons.id'), nullable=False)
    round_num    = db.Column(db.Integer, nullable=False)
    date         = db.Column(db.Date,    nullable=False)
    home_team_id = db.Column(db.Integer, db.ForeignKey('teams.id'),   nullable=False)
    away_team_id = db.Column(db.Integer, db.ForeignKey('teams.id'),   nullable=False)
    bar_id       = db.Column(db.Integer, db.ForeignKey('bars.id'),    nullable=False)

    home_team = db.relationship('Team', foreign_keys=[home_team_id])
    away_team = db.relationship('Team', foreign_keys=[away_team_id])
    bar       = db.relationship('Bar')


class Bye(db.Model):
    """
    Records a team's bye (sit-out) for a given round.

    When there's an odd number of teams, one team sits out each round. Byes
    rotate so every team sits out the same number of times across a full
    round-robin cycle. A round can have at most one bye; a team receives at
    most one bye per cycle.

    Like Match records, Byes are cascade-deleted when their season is deleted
    via the ORM — but not via bulk SQL. See the Season model docstring.
    """

    __tablename__ = 'byes'

    id        = db.Column(db.Integer, primary_key=True)
    season_id = db.Column(db.Integer, db.ForeignKey('seasons.id'), nullable=False)
    round_num = db.Column(db.Integer, nullable=False)
    date      = db.Column(db.Date,    nullable=False)
    team_id   = db.Column(db.Integer, db.ForeignKey('teams.id'),   nullable=False)

    team = db.relationship('Team')
