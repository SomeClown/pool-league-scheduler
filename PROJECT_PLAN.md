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
- Blackout date support (at season creation only — post-creation management is F-15)
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

### Planned Execution Sequence

```
F-11 (close) → F-13 (public access) → F-04 (league types) →
F-09 (mid-season regen) → F-15 (blackout date management) →
F-07 (CSV export) → F-14 (player rosters) →
F-01 (dark mode) → F-02 (mobile) → F-03 (PWA) →
F-06 (deferred — needs discovery) → F-12 (deferred — needs design)
```

Rationale: auth/routing changes first while route count is small; data model changes
before UI so design agents never retrofit new fields into styled templates; complex
back-end before UI polish; front-end redesign last.

---

### UI / UX & Platform

- [x] **F-01** — Dark-mode theme redesign. **COMPLETE (2026-08-25, commit d3ed532)**
  - Font: Inter (via Google Fonts). Permanent dark mode — no light/dark toggle needed.
  - `data-bs-theme="dark"` on `<html>`; CSS custom property token system in `app.css`;
    applied to all templates including standalone `season_compact.html` / `season_print.html`.
  - Frequency and length-mode selects replaced with Bootstrap btn-group radio groups.
  - Web Interface Guidelines review pass completed (commit e2f39dd): color-scheme, theme-color
    meta, prefers-reduced-motion guard, aria-label/aria-hidden, autocomplete attributes.

- [ ] **F-02** — Full desktop + mobile optimization (responsive at all breakpoints,
  touch-friendly controls, 44px minimum touch targets).

- [ ] **F-03** — Progressive Web App (PWA): installable on device without app store,
  service worker, manifest, offline-capable shell.
  - HTTPS is already in place on the production server (Nginx) — no server-side
    prerequisite work needed.
  - Service workers do not activate on local `flask run` (HTTP only) — this is
    expected, not a bug.
  - `start_url` should be `/seasons` (public under F-13).

---

### Schedule & League Management

- [x] **F-04** — Multiple league types per season. **COMPLETE (2026-08-25, commit 6d55a0a)**
  Initial set (Title Case, confirmed):
  - Snoqualmie Valley Men's League
  - Snoqualmie Valley Women's League
  - Snoqualmie Valley Mixed Doubles League
  - Snoqualmie Valley BCA
  League types should be extensible (admin-managed, no code changes to add more).
  - Implementation note: requires a new `LeagueType` model (new table — `db.create_all()`
    handles it), a nullable `league_type_id` FK added to `Season` (requires migration
    entry in `flask db-migrate`), a `seed-league-types` CLI command for the initial four
    rows, and admin CRUD UI. Nullable so existing seasons remain valid.

- [x] **F-05** — Maintain existing scheduling constraints and logic.

- [ ] **F-06** — Revisit and refine scheduling constraints/logic.
  - **BLOCKED** — requires a separate discovery/design session before any tasks can be
    planned. Schedule this session before implementing F-09, since constraint changes
    may affect the mid-season regeneration algorithm's state reconstruction logic.

- [x] **F-07** — Export schedules. **COMPLETE (2026-08-25)**
  - Excel export: `app/main/export.py`, colored home/away columns, week groupings, bold borders,
    Home Players / Away Players columns (added with F-14, commit 5c5fff9).
  - CSV export: `build_season_csv()` in `export.py`, UTF-8 BOM for Excel compatibility,
    public `/seasons/<id>/export/csv` route. (commit a1b36fb)

- [x] **F-08** — Regenerate schedule (full season).

- [x] **F-09** — Regenerate remaining schedule mid-season. **COMPLETE (2026-08-25, commit 23f7311)**: re-run the scheduler from
  a specified round forward, leaving all prior rounds and their dates unchanged.
  - Freeze-round selection UI: **dropdown** showing "Round N — Date" (not a text input).
  - Implementation note: requires two additive, backward-compatible changes to
    `algorithm.py` — optional `initial_history`/`initial_streaks` parameters to
    `generate_schedule()` and an optional `start_date_override` to `_map_to_dates()`.
    A new `_reconstruct_state()` helper in `routes.py` reads frozen match records to
    compute the initial history and streaks. Existing full-regeneration behavior is
    fully preserved — no callers break.

- [x] **F-10** — Team numbers displayed in schedule views.

- [x] **F-11** — Home team listed first in all schedule views.
  - **COMPLETE — audit performed during planning session (2026-08-25), no code
    changes needed.** All four output paths confirmed correct: `season_detail.html`
    (Home/Away column headers), `season_compact.html` (h × a notation, subtitle
    explicitly labels convention), `season_print.html` (`class="team home"` /
    `class="team away"`), and `export.py` ("Home #" / "Home Team" columns).

- [x] **F-15** — Post-creation blackout date management. **COMPLETE (2026-08-25, commit dbf414f)**
  - Admins must be able to add, edit, or remove blackout dates after a season has been
    created (currently only supported at creation time).
  - Adding or removing a blackout date that falls within the remaining schedule must
    offer to re-map affected round dates (shift the schedule forward or backward as
    needed). This is effectively a lightweight version of F-09 — the match assignments
    stay the same; only the calendar dates change.
  - Consider as a prerequisite or companion to F-09: the two features share the concept
    of "partial schedule modification after creation."

---

### Players & Rosters

- [x] **F-14** — Player roster per team. **COMPLETE (2026-08-25, commit 5c5fff9)**
  - Player fields: name (required), email (optional), phone (optional). Publicly visible.
  - `Player` model, `Team.players` relationship with `cascade='all, delete-orphan'`.
  - Admin CRUD via Bootstrap collapse panels in the Teams tab; edit modal for existing players.
  - Schedule detail shows collapsible roster rows per match (btn-link toggle, border-primary panel).
  - Excel export extended to 9 columns (Home Players / Away Players added).

- [ ] **F-16** — Master player database with team assignment.
  - Replace the current per-team player entry with a master `Player` table that exists
    independently of any team. Admins manage players in a dedicated "Players" admin tab.
  - Admins assign players from the master list to individual teams (many-to-one or
    many-to-many — needs design decision: can a player be on more than one team?).
  - On the public schedule view, a button per team (or per match) opens a list of players
    currently rostered on that team. Matches the existing collapsible roster panel pattern.
  - **Requires design session before implementation** — data model change is a breaking
    migration (current `Player.team_id NOT NULL` becomes a join table or nullable FK).
    Decide: (a) one active team per player at a time, or (b) players on multiple teams.
  - Note: F-14 UI patterns (collapse panels, roster rows in schedule) can be reused; only
    the data model and admin CRUD need significant rework.

---

### Public Access

- [x] **F-13** — Public schedule viewing. **COMPLETE (2026-08-25, commit d2e93fb)**
  - Season schedules, Excel export, CSV export, and the Instructions page are all
    **publicly accessible** — no login required.
  - Login button in the upper-right corner for admins and authenticated users.
    Anonymous users see a minimal navbar (brand name + Log In button only).
  - Admin-only routes (CRUD, regenerate, archive, clear schedules) remain gated.
  - Season detail page shows a **click-to-copy / copy-link button** for admins to
    easily share the public URL with league players.
  - Implementation note: remove `@login_required` from five routes (`index`, `seasons`,
    `season_detail`, `season_compact`, `season_print`, `season_export`, `instructions`).
    All existing `{% if current_user.is_admin %}` guards in templates evaluate correctly
    for anonymous users (Flask-Login's `AnonymousUser` returns falsy for admin checks).
    The `base.html` navbar's `{% if current_user.is_authenticated %}` wrapper must be
    split so anonymous users see the minimal navbar.

---

### Tournaments

- [ ] **F-12** — End-of-session seeded tournament bracket generator, one per league.
  - **DEFERRED** — pending design session with project owner. No tasks planned.

---

## Agent Architecture

Work is executed by specialized agents coordinated by the master Claude instance:

| Agent | Role | Permissions |
|---|---|---|
| **Planning** | Roadmap, task sequencing, design docs | Read-only |
| **Investigation** | Code exploration, symbol search, impact analysis | Read-only |
| **UI/UX** | Design decisions, visual language, control selection | Read-only + advisory |
| **Front-end** | Templates, CSS, JS, Bootstrap, PWA | Read + Write (UI files) |
| **Back-end** | Flask routes, models, scheduler, DB migrations | Read + Write (Python files) |

All agents are controlled from the master instance. Tasks are assigned serially
unless otherwise directed.

Note: The UI/UX agent (M-02) does not yet exist as a defined agent type. It needs
to be created as `.claude/agents/uiux.md` before F-01 work begins.

---

## Constraints & Principles

- Work in the smallest possible increments — one agent-sized chunk at a time
- No agent makes changes without an approved plan
- No breaking changes to the database schema without a migration entry in `flask db-migrate`
- No changes to `CLAUDE.md` or project config files without explicit user approval
- The scheduler must remain deterministic given the same seed inputs
- The server runs Python 3.8 — no f-string `=` debug syntax, no walrus operator,
  no 3.10+ match statements
- All exports (Excel, CSV) and all schedule views are public — no login required

---

## Change Log

| Date | Change | Agent/Author |
|---|---|---|
| 2026-08-25 | Initial PROJECT_PLAN.md created | Master (Claude) |
| 2026-08-25 | Add M-01 meta-item: custom investigation agent | Master (Claude) |
| 2026-08-25 | Planning agent audit complete; F-11 closed, execution sequence set | Planning agent |
| 2026-08-25 | Applied all planning agent suggestions; added F-15, M-02; recorded flag answers | Master (Claude) |
| 2026-08-25 | F-13 complete: public schedule viewing, anonymous navbar, copy-link button | Back-end + Front-end agents |
| 2026-08-25 | F-04 complete: league types model, CRUD, admin tab, season form, display badges | Back-end + Front-end agents |
| 2026-08-25 | F-09 complete: mid-season partial regeneration with state reconstruction | Back-end + Front-end agents |
| 2026-08-25 | F-15 complete: post-creation blackout date add/remove with full date remapping | Back-end + Front-end agents |
| 2026-08-25 | F-07 complete: CSV export route and button alongside existing Excel export | Back-end + Front-end agents |
| 2026-08-25 | F-14 complete: Player model, admin CRUD, schedule roster disclosure, Excel columns | Back-end + Front-end agents |
| 2026-08-25 | F-01 complete: dark mode, Inter font, token system, btn-group radios, guidelines pass | Front-end agent |
| 2026-08-25 | M-02 complete: UI/UX agent definition written | Master (Claude) |

---

## Meta / Tooling

These are not application features — they are improvements to the development workflow itself.

- [ ] **M-01** — Custom investigation agent (`.claude/agents/investigate.md`): a reusable
  read-only agent that onboards Claude to any codebase from cold — reads key files
  end-to-end, produces a structured summary (architecture, data flow, coupling points,
  gotchas), and flags at-risk areas before changes begin. Useful for new projects and
  new sessions starting without context.
  - Consider promoting to active: the planning session demonstrated that reading 15 files
    cold consumes significant token budget. An investigation agent could front-load this
    work once and cache the summary.

- [x] **M-02** — UI/UX agent (`.claude/agents/uiux.md`). **COMPLETE (2026-08-25)**
  - Read-only advisory agent for visual design decisions. Consulted during F-01 planning.

---

## Notes

- **F-06 and F-09 ordering:** F-06 (constraint revisit) must happen before F-09
  (mid-season regeneration) is finalized, since constraint changes could affect the
  state reconstruction logic in `_reconstruct_state()`. F-06 is currently blocked on
  a discovery session — do not begin F-09 implementation until that session is complete,
  or design F-09 with the understanding that algorithm changes may require a follow-up
  adjustment pass.
- **F-09 and F-15 relationship:** Both features deal with modifying a live season after
  creation. Consider implementing F-15 (blackout date management) immediately after F-09,
  while the partial-regeneration infrastructure is fresh.
- **F-12 (tournament):** Explicitly deferred. No planning will occur until the project
  owner initiates a design session.
- **F-03 (PWA):** HTTPS already in place on the production server. Service workers
  require HTTPS and will silently skip registration on the local `flask run` dev server —
  this is expected behavior, not a bug.
- **F-13 (public access):** Flask-Login's `AnonymousUser` object returns falsy for
  `.is_admin`, `.is_superuser`, and `.is_authenticated`. All existing admin guards in
  templates work correctly for anonymous users without modification. Only the navbar
  wrapper and five route decorators need changing.
- **F-14 (player rosters):** `Player` is a new table; `db.create_all()` creates it on
  next startup — no migration entry needed for the table itself. The `Team.players`
  relationship must use `cascade='all, delete-orphan'`. The Excel export's `_SCHED_COLS`
  constant (currently 7) must be updated to 9 when F-14-T5 runs — update all references
  to this constant carefully (affects merged title rows and `_outline_range` calls).
- **F-04 (league types):** New `LeagueType` table created by `db.create_all()`. The
  `league_type_id` FK added to `Season` requires a migration entry. Column is nullable
  so existing seasons remain valid. The four initial league types are seeded by a
  `seed-league-types` CLI command.
- **SQLite DROP COLUMN:** Available in SQLite 3.35.0+ (March 2021). If a rollback
  requiring column removal is ever needed, verify the server's SQLite version first:
  `sqlite3 --version`. If older than 3.35.0, columns cannot be dropped via SQL.
