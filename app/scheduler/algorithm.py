"""
app/scheduler/algorithm.py — Round-robin schedule generation algorithm.

This is the engine room. Given a list of teams and a target round count,
it produces a fully assigned schedule with home/away designations, bar
assignments, and calendar dates.

The process runs in three steps:
    1. _round_robin_pairs()  — generate balanced team pairings using the
                               circle method (every team plays every other
                               team exactly once per cycle)
    2. _assign_home_away()   — greedily assign home/away status to maximize
                               bar coverage (every bar active every week)
                               while respecting table capacity limits
    3. _map_to_dates()       — walk the calendar from start_date, skipping
                               blackout dates, and stamp each round with a date

If the season is long enough that teams meet more than once, the algorithm
runs additional cycles with fresh random shuffles. Home/away assignments
alternate between cycles so the same team isn't always hosting.

A note on the bar coverage goal: the algorithm tries to ensure every bar
has at least one home match per round. This is mathematically impossible to
guarantee when a bar has only one team and that team receives a bye. The
scheduler does its best; the rest is physics.
"""

import random
from datetime import timedelta


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def generate_schedule(season, teams, bars, num_rounds=None):
    """
    Generate a complete schedule for a season and return it as a list of rounds.

    Orchestrates the three-step pipeline: pairing → home/away assignment →
    calendar mapping. If num_rounds exceeds one full round-robin cycle,
    additional cycles are appended with re-shuffled team order so the
    matchup sequence varies between cycles.

    Args:
        season:     Season model instance. Needs .start_date, .frequency,
                    .blackout_dates, and .bar_caps.
        teams:      List of Team model instances participating this season.
        bars:       List of Bar model instances whose teams are in the season.
        num_rounds: Number of rounds to generate. If None, exactly one full
                    round-robin cycle is produced. If larger than one cycle,
                    additional cycles are generated until the count is met.

    Returns:
        List of round dicts:
            {
                'round_num': int,
                'date':      datetime.date,
                'matches':   [(home_team, away_team, bar_id), ...],
                'bye':       Team | None,
            }
    """
    # Per-season table caps: bar_id → tables_used (falls back to bar.tables if not set).
    bar_caps  = {cap.bar_id: cap.tables_used for cap in season.bar_caps}
    team_list = list(teams)

    # Build as many rounds as needed, repeating the round-robin with fresh
    # random shuffles each cycle so subsequent cycles vary in matchup order.
    all_rounds = []
    while num_rounds is None or len(all_rounds) < num_rounds:
        cycle_teams = list(team_list)
        random.shuffle(cycle_teams)
        cycle = _round_robin_pairs(cycle_teams)
        all_rounds.extend(cycle)
        if num_rounds is None:
            break   # One cycle only — exit after the first pass.

    # Trim to the exact requested count (the last cycle may overshoot).
    if num_rounds is not None:
        all_rounds = all_rounds[:num_rounds]

    # matchup_history tracks the last home team for each pair, so we can
    # flip home/away assignments when teams meet again in a later cycle.
    # Key: frozenset({team_a.id, team_b.id}), Value: last home team's id.
    matchup_history = {}

    assigned_rounds = []
    for r in all_rounds:
        assigned = _assign_home_away(r['pairs'], bars, bar_caps, matchup_history)
        # Update history after each round so the next encounter flips correctly.
        for home, away, _bar_id in assigned:
            matchup_history[frozenset({home.id, away.id})] = home.id
        assigned_rounds.append({'matches': assigned, 'bye': r['bye']})

    return _map_to_dates(assigned_rounds, season)


# ---------------------------------------------------------------------------
# Step 1 — Generate round-robin pairings (circle method)
# ---------------------------------------------------------------------------

def _round_robin_pairs(teams):
    """
    Generate one full round-robin cycle using the circle method.

    Every team plays every other team exactly once. For an odd number of
    teams, a None sentinel is added so every team sits out exactly once
    across the full cycle (the bye rotates each round).

    The circle method works by fixing one element and rotating the rest.
    It's a standard algorithm, not invented here — we're just using it.
    It produces N-1 rounds for N teams (or N rounds for odd N with a bye).

    Args:
        teams: List of Team instances (will be shuffled by the caller for variety)

    Returns:
        List of round dicts:
            {'pairs': [(team, team), ...], 'bye': Team | None}
    """
    n       = len(teams)
    has_bye = n % 2 == 1

    # Pad to even count with a None slot if needed.
    padded  = teams + [None] if has_bye else list(teams)
    n_pad   = len(padded)

    fixed    = padded[0]    # The fixed element — doesn't rotate.
    rotating = padded[1:]   # Everything else rotates clockwise each round.
    rounds   = []

    for _ in range(n_pad - 1):
        current  = [fixed] + rotating
        pairs    = []
        bye_team = None

        for i in range(n_pad // 2):
            t1 = current[i]
            t2 = current[n_pad - 1 - i]
            if t1 is None:
                bye_team = t2
            elif t2 is None:
                bye_team = t1
            else:
                pairs.append((t1, t2))

        rounds.append({'pairs': pairs, 'bye': bye_team})
        # Rotate: move the last element of the rotating list to the front.
        rotating = [rotating[-1]] + rotating[:-1]

    return rounds


# ---------------------------------------------------------------------------
# Step 2 — Assign home / away to maximise bar coverage
# ---------------------------------------------------------------------------

def _assign_home_away(pairs, bars, bar_caps, matchup_history):
    """
    Assign home and away status to each match pair for a single round.

    The home team's bar hosts the match. This function decides which team
    in each pair is home, balancing five priorities in order:

        1. Never exceed a bar's per-season table cap (hard limit).
        2. If this pair has met before, flip the previous home team
           (alternate home/away across multiple cycles).
        3. Prefer assigning home to a team whose bar hasn't hosted yet
           this round (bar coverage goal: every bar active every week).
        4. Among equally needy bars, prefer the one with more remaining
           table capacity.
        5. If everything else is equal, flip a coin. This ensures
           regeneration produces variety.

    Same-bar pairs (both teams share a home bar) are handled separately
    as a special case — the bar hosts regardless, and priority 2 applies
    for the home/away distinction.

    Cross-bar pairs are processed greedily with re-sorting after every
    assignment, so decisions reflect the current bar load rather than
    the state at the start of the round. This is where the magic happens,
    or at least where the interesting-ish math happens.

    Args:
        pairs:           List of (team_a, team_b) tuples for this round.
        bars:            List of Bar instances in the season.
        bar_caps:        Dict of bar_id → tables_used override.
        matchup_history: Dict of frozenset({t1.id, t2.id}) → last home team id.

    Returns:
        List of (home_team, away_team, bar_id) tuples.
    """
    # Build per-bar state tracking capacity and current load for this round.
    bar_state = {
        b.id: {'capacity': bar_caps.get(b.id, b.tables), 'load': 0}
        for b in bars
    }

    # Split pairs into same-bar (forced venue) and cross-bar (venue negotiable).
    same_bar  = [(t1, t2) for t1, t2 in pairs if t1.bar_id == t2.bar_id]
    cross_bar = [(t1, t2) for t1, t2 in pairs if t1.bar_id != t2.bar_id]

    assignments = []

    # --- Same-bar pairs: bar is fixed; only home/away needs deciding. ---
    for t1, t2 in same_bar:
        bid = t1.bar_id
        bar_state[bid]['load'] += 1
        prev_home = matchup_history.get(frozenset({t1.id, t2.id}))
        if prev_home == t1.id:
            home, away = t2, t1     # t1 was home last time — flip it.
        elif prev_home == t2.id:
            home, away = t1, t2     # t2 was home last time — flip it.
        else:
            home, away = (t1, t2) if random.random() < 0.5 else (t2, t1)
        assignments.append((home, away, bid))

    # --- Cross-bar pairs: greedy assignment with re-sort after each pick. ---
    # Re-sorting after every assignment ensures the most-urgent pair is always
    # processed next given the current bar state, not the state at round start.
    def _pair_urgency(pair):
        """
        Sort key for cross-bar pairs — lower value = process first.

        A pair is more urgent if either of its bars has a lower current load
        (i.e. hasn't hosted any matches yet this round). Processing these
        first maximises the chance of hitting the 'every bar active' goal.
        """
        t1, t2 = pair
        return min(bar_state[t1.bar_id]['load'], bar_state[t2.bar_id]['load'])

    remaining = list(cross_bar)
    while remaining:
        remaining.sort(key=_pair_urgency)
        t1, t2 = remaining.pop(0)

        b1id, b2id = t1.bar_id, t2.bar_id
        b1,   b2   = bar_state[b1id], bar_state[b2id]
        rem1       = b1['capacity'] - b1['load']
        rem2       = b2['capacity'] - b2['load']
        prev_home  = matchup_history.get(frozenset({t1.id, t2.id}))

        # Priority cascade — first condition that matches wins.
        if rem1 <= 0 and rem2 <= 0:
            # Both bars are at capacity. Host at whichever is less over-capacity.
            home, away = (t1, t2) if rem1 >= rem2 else (t2, t1)
        elif rem1 <= 0:
            home, away = t2, t1                         # bar1 is full
        elif rem2 <= 0:
            home, away = t1, t2                         # bar2 is full
        elif prev_home == t1.id:
            home, away = t2, t1                         # t1 was home last — flip
        elif prev_home == t2.id:
            home, away = t1, t2                         # t2 was home last — flip
        elif b1['load'] == 0 and b2['load'] > 0:
            home, away = t1, t2                         # bar1 needs a match
        elif b2['load'] == 0 and b1['load'] > 0:
            home, away = t2, t1                         # bar2 needs a match
        elif rem1 > rem2:
            home, away = t1, t2                         # bar1 has more room
        elif rem2 > rem1:
            home, away = t2, t1                         # bar2 has more room
        else:
            home, away = (t1, t2) if random.random() < 0.5 else (t2, t1)  # coin flip

        bar_state[home.bar_id]['load'] += 1
        assignments.append((home, away, home.bar_id))

    return assignments


# ---------------------------------------------------------------------------
# Step 3 — Map rounds to calendar dates
# ---------------------------------------------------------------------------

_FREQUENCY_DELTA = {
    'weekly':    timedelta(weeks=1),
    'biweekly':  timedelta(weeks=2),
}


def _map_to_dates(rounds, season):
    """
    Assign a calendar date to each round, skipping blackout dates.

    Walks forward from season.start_date in steps determined by the season's
    frequency, advancing past any dates in the blackout set. Each round gets
    the next available (non-blacked-out) date in sequence.

    This is a purely sequential mapping — it doesn't try to cluster rounds
    or do anything clever. Round 1 gets the first available date, round 2
    gets the next, and so on. Simple and predictable.

    Args:
        rounds: List of round dicts from _assign_home_away (no dates yet).
        season: Season instance with .start_date, .frequency, .blackout_dates.

    Returns:
        List of round dicts with 'round_num' and 'date' added.
    """
    blackouts    = {bd.date for bd in season.blackout_dates}
    delta        = _FREQUENCY_DELTA.get(season.frequency, timedelta(weeks=1))
    current_date = season.start_date
    scheduled    = []

    for i, round_data in enumerate(rounds):
        # Skip any blackout dates before landing on this round's date.
        while current_date in blackouts:
            current_date += delta

        scheduled.append({
            'round_num': i + 1,
            'date':      current_date,
            'matches':   round_data['matches'],
            'bye':       round_data['bye'],
        })
        current_date += delta

    return scheduled
