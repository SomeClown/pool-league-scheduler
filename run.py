import click
from app import create_app, db

app = create_app()


@app.shell_context_processor
def make_shell_context():
    from app.models import User, Bar, Team, Season, Match, Bye
    return {'db': db, 'User': User, 'Bar': Bar, 'Team': Team, 'Season': Season, 'Match': Match, 'Bye': Bye}


@app.cli.command('create-admin')
@click.argument('username')
@click.argument('password')
def create_admin(username, password):
    """Create an admin user. Usage: flask create-admin <username> <password>"""
    from app.models import User
    if User.query.filter_by(username=username).first():
        click.echo(f'User "{username}" already exists.')
        return
    user = User(username=username, role='admin')
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    click.echo(f'Admin user "{username}" created successfully.')


if __name__ == '__main__':
    app.run(debug=True)
