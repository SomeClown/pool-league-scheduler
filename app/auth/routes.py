"""
app/auth/routes.py — Authentication routes (login and logout).

Handles the /login and /logout endpoints. Flask-Login takes care of the
session management; we just validate credentials and tell it what to do.
Nothing fancy here — if you're looking for the interesting code, it's in
app/main/routes.py. This is just the bouncer at the door.
"""

from urllib.parse import urlparse, urljoin

from flask import render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, login_required, current_user

from app.auth import bp
from app.models import User


def _is_safe_redirect(target):
    """
    Return True if target is a relative URL on this app's own host.

    Guards against open-redirect attacks via the 'next' query parameter —
    without this check, next=https://evil.com would send a freshly
    authenticated user straight to an attacker-controlled page.
    """
    if not target:
        return False
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ('http', 'https') and ref_url.netloc == test_url.netloc


@bp.route('/login', methods=['GET', 'POST'])
def login():
    """
    Display the login form (GET) and process login credentials (POST).

    On successful authentication, redirects to the 'next' URL if one was
    provided (e.g. the user was trying to reach a protected page), otherwise
    drops them at the seasons list. On failure, flashes an error — deliberately
    vague so we don't confirm whether a username exists. Security through
    mild annoyance.

    Already-authenticated users are redirected immediately without seeing
    the form. No point showing them the door they already walked through.
    """
    if current_user.is_authenticated:
        return redirect(url_for('main.seasons'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        remember = bool(request.form.get('remember'))

        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            login_user(user, remember=remember)
            next_page = request.args.get('next')
            if not _is_safe_redirect(next_page):
                next_page = None
            return redirect(next_page or url_for('main.seasons'))

        # Don't say "wrong password" or "user not found" — combine them.
        # The first rule of authentication errors is you don't reveal which
        # part of the credentials was wrong.
        flash('Invalid username or password.', 'danger')

    return render_template('auth/login.html')


@bp.route('/logout')
@login_required
def logout():
    """
    Log out the current user and redirect to the login page.

    Simple, clean, no drama. The TARDIS dematerializes, the session ends,
    and the user is returned to the login screen as if nothing happened.
    """
    logout_user()
    return redirect(url_for('auth.login'))
