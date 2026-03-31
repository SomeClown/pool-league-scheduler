"""
Pool league schedule generator.

Round-robin pairings via the circle method, with:
  - Bye rotation for odd team counts
  - Greedy home/away assignment that maximises bar coverage per round
    while respecting each bar's table capacity
  - Calendar mapping that skips blackout dates
"""

import random
from datetime import timedelta


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def generate_schedule(season, teams, bars, num_rounds=None):
    """
    Build and return a complete schedule for *season*.

    Args:
        season     : Season model instance (needs .start_date, .frequency,
                     .blackout_dates, .bar_caps)
        teams      : list of Team model instances participating this season
        bars       : list of Bar model instances whose teams are in the season
        num_rounds : exact number of rounds to generate.  If None, one full
                     round-robin cycle is used.  If larger than one cycle,
                     additional cycles are appended (re-shuffled for variety).

    Returns:
        list of dicts:
            {
                'round_num': int,
                'date':      datetime.date,
                'matches':   [(home_team, away_team, bar_id), ...],
                'bye':       Team | None,
            }
    """
    # Per-season table caps: bar_id -> tables_used (falls back to bar.tables)
    bar_caps = {cap.bar_id: cap.tables_used for cap in season.bar_caps}

    team_list = list(teams)

    # Build as many rounds as needed, repeating the round-robin with fresh
    # random shuffles each cycle so subsequent cycles vary.
    all_rounds = []
    while num_rounds is None or len(all_rounds) < num_rounds:
        cycle_teams = list(team_list)
        random.shuffle(cycle_teams)
        cycle = _round_robin_pairs(cycle_teams)
        all_rounds.extend(cycle)
        if num_rounds is None:
            break  # one cycle only

    if num_rounds is not None:
        all_rounds = all_rounds[:num_rounds]

    # matchup_history tracks who was home the last time each pair met.
    # Used to alternate home/away when teams play each other more than once.
    matchup_history = {}  # frozenset({team_a.id, team_b.id}) -> last home team id

    assigned_rounds = []
    for r in all_rounds:
        assigned = _assign_home_away(r['pairs'], bars, bar_caps, matchup_history)
        # Update history so the next encounter for each pair flips home/away
        for home, away, _bar_id in assigned:
            matchup_history[frozenset({home.id, away.id})] = home.id
        assigned_rounds.append({'matches': assigned, 'bye': r['bye']})

    return _map_to_dates(assigned_rounds, season)


# ---------------------------------------------------------------------------
# Step 1 – generate round-robin pairings (circle method)
# ---------------------------------------------------------------------------

def _round_robin_pairs(teams):
    """
    Return a list of rounds.  Each round is:
        {'pairs': [(team, team), ...], 'bye': Team | None}

    For an odd number of teams a None sentinel is added so every team sits
    out exactly once across the full cycle.
    """
    n = len(teams)
    has_bye = n % 2 == 1

    padded = teams + [None] if has_bye else list(teams)
    n_pad = len(padded)

    fixed = padded[0]
    rotating = padded[1:]
    rounds = []

    for _ in range(n_pad - 1):
        current = [fixed] + rotating
        pairs = []
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
        # rotate: last element of rotating moves to the front
        rotating = [rotating[-1]] + rotating[:-1]

    return rounds


# ---------------------------------------------------------------------------
# Step 2 – assign home / away to maximise bar coverage
# ---------------------------------------------------------------------------

def _assign_home_away(pairs, bars, bar_caps, matchup_history):
    """
    For each (team_a, team_b) pair decide which team is *home* (and therefore
    which bar hosts the match).

    Rules, in priority order:
      1. Never exceed a bar's per-season table cap (or bar.tables if no cap set).
      2. If this pair has met before, prefer to flip the previous home team
         (alternating home/away across multiple cycles).
      3. Prefer assigning home to teams whose bar has not yet hosted a match
         this round (coverage goal: every bar active every week).
      4. Among equally needy bars, prefer the one with more remaining capacity.
      5. Break ties randomly so regeneration produces variety.

    Args:
        pairs           : list of (team_a, team_b)
        bars            : list of Bar model instances
        bar_caps        : dict of bar_id -> tables_used override (may be empty)
        matchup_history : dict of frozenset({t1.id, t2.id}) -> last home team id

    Returns list of (home_team, away_team, bar_id).
    """
    # Use per-season cap if set, otherwise fall back to bar's actual table count
    bar_state = {
        b.id: {'capacity': bar_caps.get(b.id, b.tables), 'load': 0}
        for b in bars
    }

    same_bar  = [(t1, t2) for t1, t2 in pairs if t1.bar_id == t2.bar_id]
    cross_bar = [(t1, t2) for t1, t2 in pairs if t1.bar_id != t2.bar_id]

    assignments = []

    # --- same-bar pairs: forced to their shared bar; respect history for home/away ---
    for t1, t2 in same_bar:
        bid = t1.bar_id
        bar_state[bid]['load'] += 1
        prev_home = matchup_history.get(frozenset({t1.id, t2.id}))
        if prev_home == t1.id:
            home, away = t2, t1   # flip
        elif prev_home == t2.id:
            home, away = t1, t2   # flip
        else:
            home, away = (t1, t2) if random.random() < 0.5 else (t2, t1)
        assignments.append((home, away, bid))

    # --- cross-bar pairs: greedy, re-sort after each assignment so the
    #     most-needy bar is always chosen next given the current state ---
    def _pair_urgency(pair):
        """Lower = more urgent (process first)."""
        t1, t2 = pair
        return min(bar_state[t1.bar_id]['load'], bar_state[t2.bar_id]['load'])

    remaining = list(cross_bar)
    while remaining:
        remaining.sort(key=_pair_urgency)   # re-evaluate after every assignment
        t1, t2 = remaining.pop(0)

        b1id, b2id = t1.bar_id, t2.bar_id
        b1, b2 = bar_state[b1id], bar_state[b2id]

        rem1 = b1['capacity'] - b1['load']
        rem2 = b2['capacity'] - b2['load']

        prev_home = matchup_history.get(frozenset({t1.id, t2.id}))

        if rem1 <= 0 and rem2 <= 0:
            # Both at capacity — least-over bar hosts
            home, away = (t1, t2) if rem1 >= rem2 else (t2, t1)
        elif rem1 <= 0:
            home, away = t2, t1                          # bar1 full
        elif rem2 <= 0:
            home, away = t1, t2                          # bar2 full
        elif prev_home == t1.id:
            home, away = t2, t1                          # t1 was home last time — flip
        elif prev_home == t2.id:
            home, away = t1, t2                          # t2 was home last time — flip
        elif b1['load'] == 0 and b2['load'] > 0:
            home, away = t1, t2                          # bar1 needs a match
        elif b2['load'] == 0 and b1['load'] > 0:
            home, away = t2, t1                          # bar2 needs a match
        elif rem1 > rem2:
            home, away = t1, t2
        elif rem2 > rem1:
            home, away = t2, t1
        else:
            home, away = (t1, t2) if random.random() < 0.5 else (t2, t1)

        bar_state[home.bar_id]['load'] += 1
        assignments.append((home, away, home.bar_id))

    return assignments


# ---------------------------------------------------------------------------
# Step 3 – map rounds to calendar dates
# ---------------------------------------------------------------------------

_FREQUENCY_DELTA = {
    'weekly': timedelta(weeks=1),
    'biweekly': timedelta(weeks=2),
}


def _map_to_dates(rounds, season):
    """
    Walk forward from season.start_date, skipping any blackout dates,
    and assign each round a calendar date.
    """
    blackouts = {bd.date for bd in season.blackout_dates}
    delta = _FREQUENCY_DELTA.get(season.frequency, timedelta(weeks=1))

    current_date = season.start_date
    scheduled = []

    for i, round_data in enumerate(rounds):
        while current_date in blackouts:
            current_date += delta

        scheduled.append({
            'round_num': i + 1,
            'date': current_date,
            'matches': round_data['matches'],
            'bye': round_data['bye'],
        })
        current_date += delta

    return scheduled
