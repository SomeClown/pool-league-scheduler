"""
config.py — Application configuration.

Reads sensitive values from environment variables loaded via python-dotenv.
The .env file at the project root is the right place for secrets. If you
commit your SECRET_KEY to GitHub, that's on you — on a long enough timeline,
someone will find it.
"""

import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    """
    Base configuration class. All settings read from environment variables
    with sensible (but not production-safe) fallbacks.

    In production, set SECRET_KEY and DATABASE_URL in the .env file.
    The fallback secret key is fine for local development and absolutely
    terrible for anything else.
    """

    # Flask session signing key. Change this in production or face the
    # consequences. You've been warned — twice now.
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-secret-change-in-production'

    # SQLite by default, pointed at a file in the project root.
    # Four slashes in the absolute path (sqlite:////...) is not a typo.
    # Three slashes is relative. Four is absolute. SQLAlchemy is particular about this.
    SQLALCHEMY_DATABASE_URI = (
        os.environ.get('DATABASE_URL') or
        'sqlite:///' + os.path.join(os.path.abspath(os.path.dirname(__file__)), 'league.db')
    )

    # Disable SQLAlchemy's event system for model changes — we don't use it
    # and it just eats memory. Turning it off also silences an annoying warning.
    SQLALCHEMY_TRACK_MODIFICATIONS = False
