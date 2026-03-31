# Pool League Scheduler

A web application for managing pool league seasons — generating match schedules, tracking teams and home bars, and producing print-ready schedule reports.

Built with Python (Flask), SQLite, and Bootstrap 5. Designed to be self-hosted on a Linux server.

---

## What It Does

Pool League Scheduler automates the creation of round-robin match schedules for one or more pool leagues. An admin configures bars, teams, and season parameters; the application generates a full schedule that respects bar capacities, home venue rules, blackout dates, and fair home/away rotation. League members can log in to view and print the schedule at any time.

---

## Key Features

- **Multi-season support** — create and archive as many seasons as needed; historical schedules are preserved and always accessible
- **Multiple leagues** — bars and teams are shared across seasons, so different league configurations can be scheduled independently
- **Admin / viewer roles** — admins manage everything; viewers can browse and print schedules but cannot make changes
- **Regenerate button** — re-runs the scheduling algorithm with a fresh random seed while preserving all season configuration (teams, dates, blackouts)
- **Print-ready reports** — a dedicated print view formats the schedule as a polished document; on desktop it triggers the browser print dialog automatically
- **Mobile-friendly** — the schedule view and admin pages are fully responsive

---

## Scheduling Logic

### Round-Robin Pairings
Every team plays every other team once per cycle using the **circle method** — a standard algorithm that guarantees balanced pairings across rounds. For an odd number of teams, a bye slot is added so that each team sits out exactly once per cycle.

### Multi-Cycle Seasons
If the season length (set by end date or number of weeks) spans more than one full round-robin cycle, additional cycles are appended. Each new cycle is independently shuffled so that the same matchups do not fall on the same round position each time.

### Home / Away Assignment
Each match is played at the **home team's bar**. The algorithm assigns home and away for each matchup using the following priority order:

- **Table capacity (hard constraint)** — a bar's table count (or its per-season cap, if set) is never exceeded in a single round. If a bar is full, the opposing team is assigned home instead.
- **Home/away alternation** — if two teams have met before in an earlier cycle, the team that was home last time is assigned away this time, and vice versa. This ensures fair rotation across a long season.
- **Bar coverage** — within each round, the algorithm processes matches in order of urgency, always prioritising pairs that involve a bar that has not yet hosted a match that night. This maximises the number of bars that have at least one home team playing on any given week.
- **Remaining capacity** — when multiple bars still need coverage, preference goes to the bar with more remaining table capacity.
- **Random tie-break** — all else equal, home/away is decided randomly, which contributes to variety across regenerations.

### Bar Coverage Goal
The intent is that every bar has at least one of its home teams playing there each week. This is achievable in most configurations, but has one unavoidable exception: if a bar has only one team and that team receives a bye in a given round, the bar will have no match that night. This is a mathematical consequence of odd team counts, not a scheduling defect.

### Blackout Dates
Dates entered as blackouts are skipped entirely when mapping rounds to calendar dates. The schedule advances by the configured frequency (weekly or bi-weekly) and simply steps over any blackout date, so no rounds are lost — they shift forward instead.

### Per-Season Table Caps
Each bar has a permanent table count set in the admin panel. When creating a season, admins can optionally reduce the number of tables in use for that season only (for example, using 2 of 3 available tables). The scheduling algorithm uses the lower cap for capacity enforcement without changing the bar's permanent record.

---

## Setup (Local)

```bash
git clone https://github.com/someclown/pool-league-scheduler.git
cd pool-league-scheduler
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # set SECRET_KEY
flask create-admin <username> <password>
flask run
```

Visit `http://127.0.0.1:5000` and log in.

---

## Deployment (Ubuntu / DigitalOcean)

1. Clone the repo to `/opt/pool-league-scheduler`
2. Create a virtualenv and install dependencies
3. Copy `.env.example` to `.env` and set a strong `SECRET_KEY` and absolute `DATABASE_URL`
4. Install the systemd service: `cp deploy/pool-league.service /etc/systemd/system/`
5. Configure nginx as a reverse proxy: `cp deploy/nginx.conf /etc/nginx/sites-available/pool-league`
6. Enable and start both services
7. Run `flask create-admin <username> <password>` to create the first admin user
8. Optionally install Certbot for HTTPS: `certbot --nginx -d your-domain.com`

To deploy updates:
```bash
cd /opt/pool-league-scheduler
git pull
venv/bin/pip install -r requirements.txt   # only if requirements changed
systemctl restart pool-league
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.8+ |
| Web framework | Flask |
| Database | SQLite via SQLAlchemy |
| Authentication | Flask-Login |
| Frontend | Bootstrap 5 + vanilla JS |
| Process manager | Gunicorn + systemd |
| Reverse proxy | Nginx |
