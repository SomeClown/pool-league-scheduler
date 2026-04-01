from datetime import datetime, timedelta
from functools import wraps

from flask import render_template, redirect, url_for, flash, request, abort
from flask_login import login_required, current_user

from app import db
from app.main import bp
from app.models import Bar, Bye, Match, Season, SeasonBarCap, Team, User
from app.scheduler.algorithm import generate_schedule


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _count_rounds(start_date, end_date, frequency, blackout_set):
    """Count how many match nights fall between start and end (inclusive),
    stepping by frequency and skipping blackout dates."""
    delta = timedelta(weeks=2) if frequency == 'biweekly' else timedelta(weeks=1)
    current = start_date
    count = 0
    while current <= end_date:
        if current not in blackout_set:
            count += 1
        current += delta
    return count


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return decorated


def _build_rounds(season):
    """Group a season's matches and byes into an ordered dict keyed by round."""
    rounds = {}

    for match in sorted(season.matches, key=lambda m: (m.round_num, m.bar.name, m.home_team.name)):
        r = match.round_num
        if r not in rounds:
            rounds[r] = {'matches': [], 'bye': None, 'date': match.date}
        rounds[r]['matches'].append(match)

    for bye in season.byes:
        r = bye.round_num
        if r not in rounds:
            rounds[r] = {'matches': [], 'bye': None, 'date': bye.date}
        rounds[r]['bye'] = bye

    return dict(sorted(rounds.items()))


def _persist_schedule(schedule, season):
    """Write generated schedule data to the database."""
    for round_data in schedule:
        for home_team, away_team, bar_id in round_data['matches']:
            db.session.add(Match(
                season_id=season.id,
                round_num=round_data['round_num'],
                date=round_data['date'],
                home_team_id=home_team.id,
                away_team_id=away_team.id,
                bar_id=bar_id,
            ))
        if round_data['bye']:
            db.session.add(Bye(
                season_id=season.id,
                round_num=round_data['round_num'],
                date=round_data['date'],
                team_id=round_data['bye'].id,
            ))


# ---------------------------------------------------------------------------
# Seasons
# ---------------------------------------------------------------------------

@bp.route('/')
@login_required
def index():
    return redirect(url_for('main.seasons'))


@bp.route('/seasons')
@login_required
def seasons():
    active = Season.query.filter_by(status='active').order_by(Season.created_at.desc()).all()
    archived = Season.query.filter_by(status='archived').order_by(Season.created_at.desc()).all()
    return render_template('main/seasons.html', active_seasons=active, archived_seasons=archived)


@bp.route('/seasons/new', methods=['GET', 'POST'])
@login_required
@admin_required
def season_new():
    bars = Bar.query.order_by(Bar.name).all()
    teams = Team.query.order_by(Team.name).all()

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        start_date_str = request.form.get('start_date', '').strip()
        frequency = request.form.get('frequency', 'weekly')
        length_mode = request.form.get('length_mode', 'end_date')
        end_date_str = request.form.get('end_date', '').strip()
        num_weeks_str = request.form.get('num_weeks', '').strip()
        team_ids = request.form.getlist('team_ids')
        blackout_strs = request.form.getlist('blackout_date')

        errors = []
        if not name:
            errors.append('Season name is required.')
        if not start_date_str:
            errors.append('Start date is required.')
        if len(team_ids) < 2:
            errors.append('At least 2 teams must be selected.')

        start_date = None
        if start_date_str:
            try:
                start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
            except ValueError:
                errors.append('Invalid start date.')

        # Resolve end_date from either the date picker or the weeks field
        end_date = None
        freq_delta = timedelta(weeks=2) if frequency == 'biweekly' else timedelta(weeks=1)
        if start_date:
            if length_mode == 'end_date' and end_date_str:
                try:
                    end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
                    if end_date < start_date:
                        errors.append('End date must be on or after the start date.')
                except ValueError:
                    errors.append('Invalid end date.')
            elif length_mode == 'num_weeks' and num_weeks_str:
                try:
                    num_weeks = int(num_weeks_str)
                    if num_weeks < 1:
                        errors.append('Number of weeks must be at least 1.')
                    else:
                        end_date = start_date + (num_weeks - 1) * freq_delta
                except ValueError:
                    errors.append('Number of weeks must be a whole number.')
            else:
                errors.append('Please provide an end date or number of weeks.')

        if errors:
            for e in errors:
                flash(e, 'danger')
            return render_template('main/season_new.html', bars=bars, teams=teams)

        # Parse blackout dates (needed before calculating num_rounds)
        from app.models import BlackoutDate
        blackout_set = set()
        parsed_blackouts = []
        for ds in blackout_strs:
            ds = ds.strip()
            if ds:
                try:
                    bd_date = datetime.strptime(ds, '%Y-%m-%d').date()
                    blackout_set.add(bd_date)
                    parsed_blackouts.append(BlackoutDate(date=bd_date))
                except ValueError:
                    pass

        num_rounds = _count_rounds(start_date, end_date, frequency, blackout_set)
        if num_rounds < 1:
            flash('No match nights fall within that date range. Check your dates and blackouts.', 'danger')
            return render_template('main/season_new.html', bars=bars, teams=teams)

        season = Season(name=name, start_date=start_date, end_date=end_date, frequency=frequency)
        selected_teams = Team.query.filter(Team.id.in_([int(i) for i in team_ids])).all()
        season.teams = selected_teams
        season.blackout_dates.extend(parsed_blackouts)

        db.session.add(season)
        db.session.flush()  # assign season.id before generating schedule

        bar_ids = {t.bar_id for t in selected_teams}
        bars_in_season = Bar.query.filter(Bar.id.in_(bar_ids)).all()

        # Per-season table caps — only save for bars that are actually in the season
        for bar in bars_in_season:
            submitted = request.form.get(f'bar_tables_{bar.id}', type=int)
            if submitted is not None:
                tables_used = max(1, min(submitted, bar.tables))
            else:
                tables_used = bar.tables
            season.bar_caps.append(SeasonBarCap(bar_id=bar.id, tables_used=tables_used))

        schedule = generate_schedule(season, selected_teams, bars_in_season, num_rounds=num_rounds)
        _persist_schedule(schedule, season)

        db.session.commit()
        flash(f'Season "{name}" created with {len(schedule)} rounds.', 'success')
        return redirect(url_for('main.season_detail', season_id=season.id))

    return render_template('main/season_new.html', bars=bars, teams=teams)


@bp.route('/seasons/<int:season_id>')
@login_required
def season_detail(season_id):
    season = Season.query.get_or_404(season_id)
    rounds = _build_rounds(season)
    return render_template('main/season_detail.html', season=season, rounds=rounds)


@bp.route('/seasons/<int:season_id>/print')
@login_required
def season_print(season_id):
    season = Season.query.get_or_404(season_id)
    rounds = _build_rounds(season)
    return render_template('main/season_print.html', season=season, rounds=rounds,
                           now=datetime.utcnow().date())


@bp.route('/seasons/<int:season_id>/regenerate', methods=['POST'])
@login_required
@admin_required
def season_regenerate(season_id):
    season = Season.query.get_or_404(season_id)

    if season.status == 'archived':
        flash('Archived seasons cannot be regenerated.', 'danger')
        return redirect(url_for('main.season_detail', season_id=season_id))

    Match.query.filter_by(season_id=season_id).delete()
    Bye.query.filter_by(season_id=season_id).delete()
    db.session.flush()

    bar_ids = {t.bar_id for t in season.teams}
    bars_in_season = Bar.query.filter(Bar.id.in_(bar_ids)).all()

    num_rounds = None
    if season.end_date:
        blackout_set = {bd.date for bd in season.blackout_dates}
        num_rounds = _count_rounds(season.start_date, season.end_date, season.frequency, blackout_set)

    schedule = generate_schedule(season, season.teams, bars_in_season, num_rounds=num_rounds)
    _persist_schedule(schedule, season)

    db.session.commit()
    flash('Schedule regenerated successfully.', 'success')
    return redirect(url_for('main.season_detail', season_id=season_id))


@bp.route('/seasons/<int:season_id>/archive', methods=['POST'])
@login_required
@admin_required
def season_archive(season_id):
    season = Season.query.get_or_404(season_id)
    season.status = 'archived'
    db.session.commit()
    flash(f'"{season.name}" has been archived.', 'success')
    return redirect(url_for('main.seasons'))


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------

@bp.route('/instructions')
@login_required
def instructions():
    return render_template('main/instructions.html')


@bp.route('/admin')
@login_required
@admin_required
def admin():
    bars = Bar.query.order_by(Bar.name).all()
    teams = Team.query.order_by(Team.name).all()
    users = User.query.order_by(User.username).all()
    return render_template('main/admin.html', bars=bars, teams=teams, users=users)


# --- Bars ---

@bp.route('/admin/bars/add', methods=['POST'])
@login_required
@admin_required
def bar_add():
    name = request.form.get('name', '').strip()
    tables = request.form.get('tables', 1, type=int)
    if not name:
        flash('Bar name is required.', 'danger')
    elif Bar.query.filter_by(name=name).first():
        flash(f'A bar named "{name}" already exists.', 'danger')
    else:
        db.session.add(Bar(name=name, tables=max(1, tables)))
        db.session.commit()
        flash(f'"{name}" added.', 'success')
    return redirect(url_for('main.admin') + '#bars')


@bp.route('/admin/bars/<int:bar_id>/edit', methods=['POST'])
@login_required
@admin_required
def bar_edit(bar_id):
    bar = Bar.query.get_or_404(bar_id)
    bar.name = request.form.get('name', bar.name).strip() or bar.name
    bar.tables = max(1, request.form.get('tables', bar.tables, type=int))
    db.session.commit()
    flash(f'"{bar.name}" updated.', 'success')
    return redirect(url_for('main.admin') + '#bars')


@bp.route('/admin/bars/<int:bar_id>/delete', methods=['POST'])
@login_required
@admin_required
def bar_delete(bar_id):
    bar = Bar.query.get_or_404(bar_id)
    if bar.teams:
        flash(f'Cannot delete "{bar.name}" — remove its teams first.', 'danger')
    else:
        db.session.delete(bar)
        db.session.commit()
        flash(f'"{bar.name}" deleted.', 'success')
    return redirect(url_for('main.admin') + '#bars')


# --- Teams ---

@bp.route('/admin/teams/add', methods=['POST'])
@login_required
@admin_required
def team_add():
    name = request.form.get('name', '').strip()
    bar_id = request.form.get('bar_id', type=int)
    if not name:
        flash('Team name is required.', 'danger')
    elif not bar_id:
        flash('Home bar is required.', 'danger')
    elif Team.query.filter_by(name=name).first():
        flash(f'A team named "{name}" already exists.', 'danger')
    else:
        db.session.add(Team(name=name, bar_id=bar_id))
        db.session.commit()
        flash(f'"{name}" added.', 'success')
    return redirect(url_for('main.admin') + '#teams')


@bp.route('/admin/teams/<int:team_id>/edit', methods=['POST'])
@login_required
@admin_required
def team_edit(team_id):
    team = Team.query.get_or_404(team_id)
    team.name = request.form.get('name', team.name).strip() or team.name
    team.bar_id = request.form.get('bar_id', team.bar_id, type=int)
    db.session.commit()
    flash(f'"{team.name}" updated.', 'success')
    return redirect(url_for('main.admin') + '#teams')


@bp.route('/admin/teams/<int:team_id>/delete', methods=['POST'])
@login_required
@admin_required
def team_delete(team_id):
    team = Team.query.get_or_404(team_id)
    if team.seasons:
        flash(f'Cannot delete "{team.name}" — it belongs to one or more seasons.', 'danger')
    else:
        db.session.delete(team)
        db.session.commit()
        flash(f'"{team.name}" deleted.', 'success')
    return redirect(url_for('main.admin') + '#teams')


# --- Users ---

@bp.route('/admin/users/add', methods=['POST'])
@login_required
@admin_required
def user_add():
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '')
    role = request.form.get('role', 'viewer')
    if not username or not password:
        flash('Username and password are required.', 'danger')
    elif User.query.filter_by(username=username).first():
        flash(f'Username "{username}" is already taken.', 'danger')
    else:
        user = User(username=username, role=role)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        flash(f'User "{username}" added.', 'success')
    return redirect(url_for('main.admin') + '#users')


@bp.route('/admin/users/<int:user_id>/edit', methods=['POST'])
@login_required
@admin_required
def user_edit(user_id):
    user = User.query.get_or_404(user_id)
    user.username = request.form.get('username', user.username).strip() or user.username
    user.role = request.form.get('role', user.role)
    new_password = request.form.get('password', '').strip()
    if new_password:
        user.set_password(new_password)
    db.session.commit()
    flash(f'User "{user.username}" updated.', 'success')
    return redirect(url_for('main.admin') + '#users')


@bp.route('/admin/users/<int:user_id>/delete', methods=['POST'])
@login_required
@admin_required
def user_delete(user_id):
    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        flash("You cannot delete your own account.", 'danger')
    else:
        db.session.delete(user)
        db.session.commit()
        flash(f'User "{user.username}" deleted.', 'success')
    return redirect(url_for('main.admin') + '#users')
