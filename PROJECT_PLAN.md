# Snoqualmie Valley Pool League Scheduler — Project Plan

## Overview

A Flask web application for managing pool league schedules for the Snoqualmie Valley
area. Admins configure bars, teams, and season parameters; the app produces round-robin
match schedules respecting bar capacities, home venue rules, blackout dates, and
home/away alternation across multiple cycles.

**Stack:** Python 3.8+ · Flask 3.0.3 · SQLite/SQLAlchemy · Bootstrap 5 · Gunicorn/Nginx
**Repo:** github.com/SomeClown/pool-league-scheduler
**Deployment:** DigitalOcean droplet at tubgoat.com

---

## Current State

The following features are complete and deployed:

- Round-robin schedule generation (circle method, multi-cycle support)
- Home/away assignment with bar coverage optimization and streak limiting
- Per-bar table capacity limits (global and per-season overrides)
- Blackout date support
- Season archiving
- Team numbering (`#N Team Name` display)
- Compact quick-reference schedule view (team number × team number)
- Excel export with colored columns, week groupings, bold borders
- Print-optimized schedule view
- Three-tier user roles: viewer / admin / superuser
- Change-password for all users
- "Clear All Schedules" nuclear option (superuser only)
- Instructions/help page
- Responsive Bootstrap 5 layout (functional, not yet design-polished)

---

## Feature Roadmap

Items are listed as received. The planning agent is responsible for sequencing,
breaking into tasks, and assigning to agents. Status: `[ ]` not started,
`[~]` partial/existing, `[x]` complete.

### UI / UX & Platform

- [ ] **F-01** — Dark-mode theme redesign: modern fonts, icons, radial buttons,
  sliders, consistent visual language throughout
- [ ] **F-02** — Full desktop + mobile optimization (responsive at all breakpoints,
  touch-friendly controls)
- [ ] **F-03** — Progressive Web App (PWA): installable on device without app store,
  service worker, manifest, offline-capable shell

### Schedule & League Management

- [ ] **F-04** — Multiple league types per season. Initial set:
  - Snoqualmie Valley Men's League
  - Snoqualmie Valley Women's League
  - Snoqualmie Valley Mixed Doubles League
  - Snoqualmie Valley BCA
  League types should be extensible (add more later without code changes).
- [x] **F-05** — Maintain existing scheduling constraints and logic
- [ ] **F-06** — Revisit and refine scheduling constraints/logic before declaring
  the scheduler feature-complete. Requires a separate discovery/design session.
- [~] **F-07** — Export schedules (Excel implemented; consider CSV/Google Sheets
  compatibility as an additional format option)
- [x] **F-08** — Regenerate schedule (full season)
- [ ] **F-09** — Regenerate remaining schedule mid-season: re-run the scheduler
  from a specified round forward, leaving all prior rounds and their dates unchanged
- [x] **F-10** — Team numbers displayed in schedule views
- [ ] **F-11** — Confirm home team is always listed first in all schedule views
  (audit all templates and the compact view)

### Players & Rosters

- [ ] **F-14** — Player roster per team: add individual player names to each team
  so players can identify opponents when preparing for a match. Visible in schedule
  views and potentially on a team detail page.

### Public Access

- [ ] **F-13** — Public schedule viewing: season schedules visible to anyone without
  requiring a login. Login button remains in the upper-right corner for admins and
  authenticated users. League players get a direct URL to view the current season
  schedule with no account required.

### Tournaments

- [ ] **F-12** — End-of-session seeded tournament bracket generator, one per league.
  Design TBD — defer until other features are complete.

---

## Agent Architecture

Work is executed by specialized agents coordinated by the master Claude instance:

| Agent | Role | Permissions |
|---|---|---|
| **Planning** | Roadmap, task sequencing, design docs | Read-only |
| **Investigation** | Code exploration, symbol search, impact analysis | Read-only |
| **Front-end** | Templates, CSS, JS, Bootstrap, PWA | Read + Write (UI files) |
| **Back-end** | Flask routes, models, scheduler, DB migrations | Read + Write (Python files) |

All agents are controlled from the master instance. Tasks are assigned serially
unless otherwise directed.

---

## Constraints & Principles

- Work in the smallest possible increments — one agent-sized chunk at a time
- No agent makes changes without an approved plan
- No breaking changes to the database schema without a migration entry in `flask db-migrate`
- No changes to `CLAUDE.md` or project config files without explicit user approval
- The scheduler must remain deterministic given the same seed inputs
- The server runs Python 3.8 — no f-string `=` debug syntax, no walrus operator,
  no 3.10+ match statements

---

## Change Log

| Date | Change | Agent/Author |
|---|---|---|
| 2026-08-25 | Initial PROJECT_PLAN.md created | Master (Claude) |

---

## Notes

- Mid-schedule regeneration (F-09) is the most complex new back-end feature.
  It requires the scheduler to accept a "freeze through round N" parameter and
  to avoid re-using dates already assigned to frozen rounds.
- F-06 (constraint revisit) should happen before F-09, since any logic changes
  would affect what the regenerated portion produces.
- F-12 (tournament) is explicitly deferred until the user has completed design
  thinking on it.
- The PWA (F-03) is a front-end concern but requires HTTPS on the server, which
  is already in place via Nginx + the existing deployment.
- Public access (F-13) requires careful route-level auth review — currently all
  routes are behind @login_required. Flask-Login supports anonymous access via
  @login_required removal + current_user.is_authenticated checks in templates.
- Player rosters (F-14) will require a new Player model, a migration, and UI
  for adding/editing players on the team admin page.
