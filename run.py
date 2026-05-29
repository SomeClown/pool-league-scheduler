"""
run.py — Application entry point and development server launcher.

In production, Gunicorn points at 'run:app' directly and never touches the
__main__ block at the bottom. In development, 'python run.py' spins up
Flask's built-in server with debug mode on. Do not run debug=True in
production. This is not a suggestion.

The shell context processor at the bottom is a quality-of-life feature for
'flask shell' sessions — it pre-imports all the important models so you're
not typing 'from app.models import ...' at 3am while you debug something.
"""

import click
from app import create_app, db

app = create_app()


@app.shell_context_processor
def make_shell_context():
    """
    Pre-load models and the db session into the flask shell namespace.

    Saves you from having to import everything manually every time you
    open a shell to poke at the database. Think of it as the developer
    equivalent of Sheldon's spot on the couch — everything is exactly
    where it should be.
    """
    from app.models import User, Bar, Team, Season, Match, Bye
    return {
        'db': db,
        'User': User,
        'Bar': Bar,
        'Team': Team,
        'Season': Season,
        'Match': Match,
        'Bye': Bye,
    }


if __name__ == '__main__':
    # Development only. If you're running this in production, we need to talk.
    app.run(debug=True)
