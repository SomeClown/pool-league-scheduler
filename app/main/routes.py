"""
app/main/routes.py — All application routes except authentication.

This is where everything actually happens. Seasons, schedules, admin
management, user management, exports — all of it lives here. If something
is broken, there's a good chance the answer is somewhere in this file.

Route structure at a glance:
    /                            → redirects to /seasons
    /seasons                     → season list
    /seasons/new                 → create a new season
    /seasons/<id>                → season detail / schedule view
    /seasons/<id>/print          → print-formatted schedule
    /seasons/<id>/export         → download Excel spreadsheet
    /seasons/<id>/compact        → compact quick-reference view
    /seasons/<id>/regenerate     → re-run the scheduler (admin)
    /seasons/<id>/archive        → archive a season (admin)
    /instructions                → help page
    /admin                       → admin panel (bars / teams / users)
    /admin/bars/...              → bar CRUD
    /admin/teams/...             → team CRUD
    /admin/users/...             → user CRUD
    /admin/clear-schedules       → nuclear option (superuser only)
    /account/password            → change your own password
"""

from datetime import datetime, timedelta
from functools import wraps

from flask import render_template, redirect, url_for, flash, request, abort, send_file
from flask_login import login_required, current_user

from app import db
from app.main import bp
from app.models import Bar, Bye, Match, Season, SeasonBarCap, Team, User
from app.scheduler.algorithm import generate_schedule


# ---------------------------------------------------------------------------
# Decorators and helper functions
# ---------------------------------------------------------------------------

def admin_required(f):
    """
    Route decorator that restricts access to admin-level users and above.

    Returns a 403 Forbidden if the current user is a viewer. Superusers
    pass this check automatically since is_admin covers them too.
    Stack this decorator below @login_required.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return decorated


def superuser_required(f):
    """
    Route decorator that restricts access to superusers only.

    More restrictive than admin_required — regular admins are turned away
    at the door. Reserved for operations that can't be undone, like wiping
    all schedule data. With great power comes great responsibility, and also
    a confirmation modal.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_superuser:
            abort(403)
        return f(*args, **kwargs)
    return decorated


def _count_rounds(start_date, end_date, frequency, blackout_set):
    """
    Count how many valid match nights fall in a date range.

    Walks from start_date to end_date in steps of one or two weeks
    (depending on frequency), skipping any dates in blackout_set.
    Returns the count of non-blacked-out dates — this is how many rounds
    the scheduler will generate.

    Args:
        start_date:   First possible match date (datetime.date)
        end_date:     Last possible match date, inclusive (datetime.date)
        frequency:    'weekly' or 'biweekly'
        blackout_set: set of datetime.date objects to skip

    Returns:
        int — number of schedulable match nights
    """
    delta   = timedelta(weeks=2) if frequency == 'biweekly' else timedelta(weeks=1)
    current = start_date
    count   = 0
    while current <= end_date:
        if current not in blackout_set:
            count += 1
        current += delta
    return count


def _build_rounds(season):
    """
    Assemble a season's matches and byes into an ordered dict keyed by round number.

    Queries the season's matches and byes from the database and groups them
    by round_num. Matches within each round are sorted by bar name then home
    team name for consistent display ordering. Returns an OrderedDict-style
    plain dict sorted by round number.

    The return structure looks like:
        {
            1: {'matches': [Match, ...], 'bye': Bye|None, 'date': date},
            2: {'matches': [Match, ...], 'bye': Bye|None, 'date': date},
            ...
        }

    Note: if match.bar or match.home_team is None (orphaned FK — shouldn't
    happen with clean data, but SQLite doesn't enforce constraints by default),
    those fields are treated as empty strings in the sort key to avoid an
    AttributeError. Wibbly-wobbly data-wata.
    """
    rounds = {}

    for match in sorted(season.matches, key=lambda m: (
            m.round_num,
            m.bar.name       if m.bar       else '',
            m.home_team.name if m.home_team else '')):
        r = match.round_num
        if r not in rounds:
            rounds[r] = {'matches': [], 'bye': None, 'date': match.date}
        rounds[r]['matches'].append(match)

    for bye in season.byes:
        r = bye.round_num
        if r not in rounds:
            # Edge case: a round that has only a bye and no matches.
            # Shouldn't happen in practice since odd-team byes still have matches.
            rounds[r] = {'matches': [], 'bye': None, 'date': bye.date}
        rounds[r]['bye'] = bye

    return dict(sorted(rounds.items()))


def _persist_schedule(schedule, season):
    """
    Write a generated schedule to the database.

    Takes the list of round dicts returned by generate_schedule() and creates
    Match and Bye ORM objects for each entry. Does NOT commit — the caller is
    responsible for calling db.session.commit() after this returns. This keeps
    the whole season creation/regeneration transactional.

    Args:
        schedule: list of round dicts from generate_schedule()
        season:   the Season instance (must already be flushed so season.id exists)
    """
    for round_data in schedule:
        for home_team, away_team, bar_id in round_data['matches']:
            db.session.add(Match(
                season_id    = season.id,
                round_num    = round_data['round_num'],
                date         = round_data['date'],
                home_team_id = home_team.id,
                away_team_id = away_team.id,
                bar_id       = bar_id,
            ))
        if round_data['bye']:
            db.session.add(Bye(
                season_id = season.id,
                round_num = round_data['round_num'],
                date      = round_data['date'],
                team_id   = round_data['bye'].id,
            ))


# ---------------------------------------------------------------------------
# Season routes
# ---------------------------------------------------------------------------

@bp.route('/')
def index():
    """Root URL — just redirects to the seasons list. Nothing to see here."""
    return redirect(url_for('main.seasons'))


@bp.route('/seasons')
def seasons():
    """
    Display the main seasons list page.

    Shows active seasons first, then archived seasons. Both groups are sorted
    newest-first by creation date. This is the landing page after login.
    """
    active   = Season.query.filter_by(status='active').order_by(Season.created_at.desc()).all()
    archived = Season.query.filter_by(status='archived').order_by(Season.created_at.desc()).all()
    return render_template('main/seasons.html', active_seasons=active, archived_seasons=archived)


@bp.route('/seasons/new', methods=['GET', 'POST'])
@login_required
@admin_required
def season_new():
    """
    Display the new season form (GET) and process the submission (POST).

    On GET: renders the form pre-populated with all bars and teams.
    On POST: validates inputs, resolves the end date from either a calendar
    picker or a week count, parses blackout dates, creates the Season record,
    generates the full schedule via the scheduler algorithm, and persists
    everything in a single transaction.

    Season length can be specified two ways:
        end_date  — pick a specific calendar date
        num_weeks — enter a number of weeks; end date is calculated from that

    Table caps (per-season overrides for bar table counts) are read from
    form fields named 'bar_tables_<bar_id>' and stored as SeasonBarCap records.

    The whole thing — season, teams, blackouts, bar caps, matches, byes — is
    committed atomically. If the scheduler blows up, nothing is saved.
    """
    bars  = Bar.query.order_by(Bar.name).all()
    teams = Team.query.order_by(Team.name).all()

    if request.method == 'POST':
        name           = request.form.get('name', '').strip()
        start_date_str = request.form.get('start_date', '').strip()
        frequency      = request.form.get('frequency', 'weekly')
        length_mode    = request.form.get('length_mode', 'end_date')
        end_date_str   = request.form.get('end_date', '').strip()
        num_weeks_str  = request.form.get('num_weeks', '').strip()
        team_ids       = request.form.getlist('team_ids')
        blackout_strs  = request.form.getlist('blackout_date')

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

        # Resolve end_date — either directly from the date picker or calculated
        # from a week count. Both UI controls stay in sync via JavaScript, so
        # the user should rarely need to think about this.
        end_date   = None
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

        # Parse blackout dates — we need them before counting rounds.
        # Invalid date strings are silently ignored (the JS date picker
        # should prevent garbage input, but here be dragons).
        from app.models import BlackoutDate
        blackout_set    = set()
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

        # Build the season object and flush to get an ID before the scheduler runs.
        season         = Season(name=name, start_date=start_date, end_date=end_date, frequency=frequency)
        selected_teams = Team.query.filter(Team.id.in_([int(i) for i in team_ids])).all()
        season.teams   = selected_teams
        season.blackout_dates.extend(parsed_blackouts)

        db.session.add(season)
        db.session.flush()  # Need season.id before we can create Match/Bye records.

        bar_ids       = {t.bar_id for t in selected_teams}
        bars_in_season = Bar.query.filter(Bar.id.in_(bar_ids)).all()

        # Store per-season table caps for each bar in this season.
        # The bar's standing tables-in-use limit is a hard cap: the submitted
        # value is clamped to [1, bar.tables_in_use], so a season can use
        # fewer tables than the bar allows but never more. Raising the limit
        # requires editing the bar itself in Admin.
        for bar in bars_in_season:
            cap_limit   = bar.tables_in_use or bar.tables
            submitted   = request.form.get(f'bar_tables_{bar.id}', type=int)
            tables_used = max(1, min(submitted, cap_limit)) if submitted is not None else cap_limit
            season.bar_caps.append(SeasonBarCap(bar_id=bar.id, tables_used=tables_used))

        schedule = generate_schedule(season, selected_teams, bars_in_season, num_rounds=num_rounds)
        _persist_schedule(schedule, season)

        db.session.commit()
        flash(f'Season "{name}" created with {len(schedule)} rounds.', 'success')
        return redirect(url_for('main.season_detail', season_id=season.id))

    return render_template('main/season_new.html', bars=bars, teams=teams)


@bp.route('/seasons/<int:season_id>')
def season_detail(season_id):
    """
    Display the full schedule for a season, organized by round.

    Loads the season, builds the rounds dict via _build_rounds(), and
    passes it to the template. This is the main "look at the schedule" page.
    """
    season = Season.query.get_or_404(season_id)
    rounds = _build_rounds(season)
    return render_template('main/season_detail.html', season=season, rounds=rounds)


@bp.route('/seasons/<int:season_id>/print')
def season_print(season_id):
    """
    Render a print-optimized version of the season schedule.

    Opens as a standalone page (no navbar) and auto-triggers the browser
    print dialog on desktop. On mobile, a Print button is shown instead
    since auto-triggering print on mobile is more annoying than helpful.
    """
    season = Season.query.get_or_404(season_id)
    rounds = _build_rounds(season)
    return render_template('main/season_print.html', season=season, rounds=rounds,
                           now=datetime.utcnow().date())


@bp.route('/seasons/<int:season_id>/export')
def season_export(season_id):
    """
    Generate and return a formatted Excel (.xlsx) workbook for the season.

    The workbook contains three sheets: Schedule (full match listing with
    colour-coded columns and week groupings), Teams (numbered roster), and
    Bars (venue list). The file is returned as an attachment download.

    Excel generation is handled by app/main/export.py using openpyxl.
    The filename is derived from the season name with spaces replaced by
    underscores because spaces in filenames are uncivilized.
    """
    from app.main.export import build_season_excel
    season   = Season.query.get_or_404(season_id)
    rounds   = _build_rounds(season)
    output   = build_season_excel(season, rounds)
    filename = season.name.replace(' ', '_') + '_Schedule.xlsx'
    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=filename,
    )


@bp.route('/seasons/<int:season_id>/compact')
def season_compact(season_id):
    """
    Render the compact quick-reference view for a season.

    Shows each week as a single row with matches in 'NxN' format (home number
    × away number). Includes a team key at the top mapping numbers to names.
    Designed to fit on a single printed page for players who just want to know
    who they're playing and when.

    Teams are pre-sorted by number (None last, alphabetically within each group)
    in Python rather than in the template, because Jinja2's sort filter can't
    handle None values mixed with integers without a meltdown.
    """
    season       = Season.query.get_or_404(season_id)
    rounds       = _build_rounds(season)
    sorted_teams = sorted(season.teams, key=lambda t: (t.number is None, t.number or 0, t.name))
    return render_template('main/season_compact.html', season=season, rounds=rounds,
                           sorted_teams=sorted_teams)


@bp.route('/seasons/<int:season_id>/regenerate', methods=['POST'])
@login_required
@admin_required
def season_regenerate(season_id):
    """
    Re-run the scheduling algorithm for an existing season, replacing all matches.

    Deletes the current match and bye records, then calls generate_schedule()
    with the same teams, bars, and round count. The random shuffle inside the
    scheduler means you'll get a different arrangement each time. All other
    season data (name, dates, blackouts, bar caps, team list) is preserved.

    Archived seasons cannot be regenerated — they're locked for historical
    accuracy. This is by design, not an oversight.
    """
    season = Season.query.get_or_404(season_id)

    if season.status == 'archived':
        flash('Archived seasons cannot be regenerated.', 'danger')
        return redirect(url_for('main.season_detail', season_id=season_id))

    # Wipe existing schedule data. Using query.delete() here is intentional —
    # we're deleting by season_id filter, not the whole table, so ORM cascade
    # isn't needed. Flush before regenerating to avoid stale data.
    Match.query.filter_by(season_id=season_id).delete()
    Bye.query.filter_by(season_id=season_id).delete()
    db.session.flush()

    bar_ids        = {t.bar_id for t in season.teams}
    bars_in_season = Bar.query.filter(Bar.id.in_(bar_ids)).all()

    num_rounds = None
    if season.end_date:
        blackout_set = {bd.date for bd in season.blackout_dates}
        num_rounds   = _count_rounds(season.start_date, season.end_date, season.frequency, blackout_set)

    schedule = generate_schedule(season, season.teams, bars_in_season, num_rounds=num_rounds)
    _persist_schedule(schedule, season)

    db.session.commit()
    flash('Schedule regenerated successfully.', 'success')
    return redirect(url_for('main.season_detail', season_id=season_id))


@bp.route('/seasons/<int:season_id>/archive', methods=['POST'])
@login_required
@admin_required
def season_archive(season_id):
    """
    Archive a season, making it read-only.

    Sets status to 'archived'. Archived seasons are still viewable and
    printable but cannot be regenerated. The operation is one-way through
    the UI — there's no un-archive button — which is intentional. Historical
    records should stay historical.
    """
    season        = Season.query.get_or_404(season_id)
    season.status = 'archived'
    db.session.commit()
    flash(f'"{season.name}" has been archived.', 'success')
    return redirect(url_for('main.seasons'))


# ---------------------------------------------------------------------------
# Static / informational routes
# ---------------------------------------------------------------------------

@bp.route('/instructions')
def instructions():
    """
    Render the instructions / help page.

    Viewers see a guide to reading the schedule. Admins see the full guide
    including setup instructions, season configuration options, and notes on
    scheduling logic. The template handles role-based section visibility.
    """
    return render_template('main/instructions.html')


# ---------------------------------------------------------------------------
# Admin panel routes
# ---------------------------------------------------------------------------

@bp.route('/admin')
@login_required
@admin_required
def admin():
    """
    Render the admin panel with tabs for Bars, Teams, and Users.

    Teams are sorted by number (numbered teams first, unnumbered last,
    alphabetically within each group). This sort happens in Python because
    SQLAlchemy's order_by can't cleanly handle nullable integer columns the
    way we want.
    """
    bars  = Bar.query.order_by(Bar.name).all()
    teams = Team.query.order_by(Team.name).all()
    teams.sort(key=lambda t: (t.number is None, t.number or 0, t.name))
    users = User.query.order_by(User.username).all()
    return render_template('main/admin.html', bars=bars, teams=teams, users=users)


# --- Bars ---

@bp.route('/admin/bars/add', methods=['POST'])
@login_required
@admin_required
def bar_add():
    """
    Create a new bar / venue.

    Requires a unique name and a table count (minimum 1). Duplicate names
    are rejected. Table count is clamped to at least 1 because a bar with
    zero tables is just a bar, and we have enough of those already.

    'Tables in use' (how many tables the league schedules on) is optional
    and clamped to [1, tables]; it defaults to the full table count.
    """
    name   = request.form.get('name', '').strip()
    tables = max(1, request.form.get('tables', 1, type=int))
    in_use = request.form.get('tables_in_use', type=int)
    in_use = max(1, min(in_use, tables)) if in_use is not None else tables
    if not name:
        flash('Bar name is required.', 'danger')
    elif Bar.query.filter_by(name=name).first():
        flash(f'A bar named "{name}" already exists.', 'danger')
    else:
        db.session.add(Bar(name=name, tables=tables, tables_in_use=in_use))
        db.session.commit()
        flash(f'"{name}" added.', 'success')
    return redirect(url_for('main.admin') + '#bars')


@bp.route('/admin/bars/<int:bar_id>/edit', methods=['POST'])
@login_required
@admin_required
def bar_edit(bar_id):
    """
    Update an existing bar's name, table count, and/or tables in use.

    Table count is clamped to a minimum of 1, and tables-in-use to
    [1, tables] — so lowering the physical count automatically pulls the
    in-use limit down with it. Note that reducing these below what an active
    season's cap is set to won't automatically update those caps — that's
    a you-problem to sort out manually.
    """
    bar        = Bar.query.get_or_404(bar_id)
    bar.name   = request.form.get('name', bar.name).strip() or bar.name
    bar.tables = max(1, request.form.get('tables', bar.tables, type=int))
    in_use     = request.form.get('tables_in_use', type=int)
    if in_use is None:
        in_use = bar.tables_in_use or bar.tables
    bar.tables_in_use = max(1, min(in_use, bar.tables))
    db.session.commit()
    flash(f'"{bar.name}" updated.', 'success')
    return redirect(url_for('main.admin') + '#bars')


@bp.route('/admin/bars/<int:bar_id>/delete', methods=['POST'])
@login_required
@admin_required
def bar_delete(bar_id):
    """
    Delete a bar, provided it has no teams assigned to it.

    A bar with teams cannot be deleted — reassign or delete the teams first.
    This prevents orphaned team records pointing at a non-existent bar, which
    would cause the kind of database integrity issues that ruin your Monday.
    """
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
    """
    Create a new team and assign it to a home bar.

    Team number is optional — leave it blank and it stays null. Names must
    be unique across all teams. A home bar is required; the bar determines
    where the team's home matches are played.
    """
    name   = request.form.get('name', '').strip()
    bar_id = request.form.get('bar_id', type=int)
    number = request.form.get('number', type=int)
    if not name:
        flash('Team name is required.', 'danger')
    elif not bar_id:
        flash('Home bar is required.', 'danger')
    elif Team.query.filter_by(name=name).first():
        flash(f'A team named "{name}" already exists.', 'danger')
    else:
        db.session.add(Team(name=name, bar_id=bar_id, number=number))
        db.session.commit()
        flash(f'"{name}" added.', 'success')
    return redirect(url_for('main.admin') + '#teams')


@bp.route('/admin/teams/<int:team_id>/edit', methods=['POST'])
@login_required
@admin_required
def team_edit(team_id):
    """
    Update a team's name, home bar, and/or league number.

    Submitting an empty number field clears the number (sets it to None).
    Submitting a value sets or replaces it. The number is purely cosmetic —
    it doesn't affect scheduling logic, only display.
    """
    team         = Team.query.get_or_404(team_id)
    team.name    = request.form.get('name', team.name).strip() or team.name
    team.bar_id  = request.form.get('bar_id', team.bar_id, type=int)
    raw_number   = request.form.get('number', '').strip()
    team.number  = int(raw_number) if raw_number else None
    db.session.commit()
    flash(f'"{team.name}" updated.', 'success')
    return redirect(url_for('main.admin') + '#teams')


@bp.route('/admin/teams/<int:team_id>/delete', methods=['POST'])
@login_required
@admin_required
def team_delete(team_id):
    """
    Delete a team, provided it doesn't belong to any seasons.

    A team enrolled in one or more seasons cannot be deleted — remove it
    from those seasons first (or clear the schedule data if you're starting
    fresh). This is a safety guard against orphaned match records.
    """
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
    """
    Create a new user account.

    Regular admins can only create viewer accounts. Only the superuser can
    create admin accounts. Usernames must be unique. This is not negotiable —
    ask the database, not me.

    Passwords are hashed immediately on creation. The plain-text password is
    not stored anywhere. If a user forgets their password, an admin can reset
    it via the Edit User form.
    """
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '')
    role     = request.form.get('role', 'viewer')
    if not username or not password:
        flash('Username and password are required.', 'danger')
    elif role == 'admin' and not current_user.is_superuser:
        flash('Only the superuser can create admin accounts.', 'danger')
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
    """
    Update a user's username, role, and/or password.

    Regular admins cannot edit admin accounts — that requires superuser.
    Regular admins also cannot escalate a viewer to admin — the role change
    is silently ignored if they try. The superuser account's own role and
    superuser flag can only be changed via the CLI (make-superuser).

    Password field is optional — leave it blank to keep the existing password.
    """
    user = User.query.get_or_404(user_id)
    if user.is_admin and not current_user.is_superuser:
        flash('Only the superuser can edit admin accounts.', 'danger')
        return redirect(url_for('main.admin') + '#users')

    user.username = request.form.get('username', user.username).strip() or user.username
    new_role      = request.form.get('role', user.role)

    # Non-superusers can't promote anyone to admin, even if they somehow
    # submit that value. Silently revert to the existing role.
    if new_role == 'admin' and not current_user.is_superuser:
        new_role = user.role
    user.role = new_role

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
    """
    Delete a user account.

    Three things prevent deletion:
        1. You cannot delete yourself. (Don't even try.)
        2. The superuser account cannot be deleted through the UI.
           Use the database directly if you're absolutely sure.
        3. Regular admins cannot delete other admin accounts.
           Only the superuser can do that.

    Everyone else is fair game.
    """
    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        flash("You cannot delete your own account.", 'danger')
    elif user.is_superuser:
        flash("The superuser account cannot be deleted.", 'danger')
    elif user.is_admin and not current_user.is_superuser:
        flash('Only the superuser can delete admin accounts.', 'danger')
    else:
        db.session.delete(user)
        db.session.commit()
        flash(f'User "{user.username}" deleted.', 'success')
    return redirect(url_for('main.admin') + '#users')


@bp.route('/admin/clear-schedules', methods=['POST'])
@login_required
@superuser_required
def clear_all_schedules():
    """
    Permanently delete all seasons, matches, byes, and related data.

    This is the nuclear option. Bars and teams are untouched; everything
    else goes. Superuser only, and gated behind a confirmation modal in
    the UI because 'are you sure?' is a reasonable question.

    Uses explicit SQL DELETEs in dependency order rather than ORM cascade,
    because SQLAlchemy's bulk query.delete() bypasses cascade rules entirely.
    Learned that one the hard way. Don't let SQLite's lax FK enforcement
    fool you into thinking it doesn't matter — it does, eventually.

    "On a long enough timeline, the data loss rate for everyone drops to zero."
    — Someone who didn't back up their database.
    """
    from sqlalchemy import text
    db.session.execute(text('DELETE FROM byes'))
    db.session.execute(text('DELETE FROM matches'))
    db.session.execute(text('DELETE FROM blackout_dates'))
    db.session.execute(text('DELETE FROM season_bar_caps'))
    db.session.execute(text('DELETE FROM season_teams'))
    db.session.execute(text('DELETE FROM seasons'))
    db.session.commit()
    flash('All schedules have been deleted. Bars and teams are intact.', 'success')
    return redirect(url_for('main.admin'))


@bp.route('/account/password', methods=['GET', 'POST'])
@login_required
def change_password():
    """
    Allow the currently logged-in user to change their own password.

    Available to all roles — viewers, admins, superusers. Requires the
    current password for verification (so someone who walked away from an
    unlocked browser can't silently change your credentials). New password
    must be at least 8 characters and confirmed by re-entry.

    On success, redirects to the seasons page. On failure, re-renders the
    form with an appropriate flash message. Bazinga.
    """
    if request.method == 'POST':
        current_pw = request.form.get('current_password', '')
        new_pw     = request.form.get('new_password', '')
        confirm_pw = request.form.get('confirm_password', '')

        if not current_user.check_password(current_pw):
            flash('Current password is incorrect.', 'danger')
        elif len(new_pw) < 8:
            flash('New password must be at least 8 characters.', 'danger')
        elif new_pw != confirm_pw:
            flash('New passwords do not match.', 'danger')
        else:
            current_user.set_password(new_pw)
            db.session.commit()
            flash('Password updated successfully.', 'success')
            return redirect(url_for('main.seasons'))

    return render_template('main/change_password.html')
