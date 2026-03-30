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

def generate_schedule(season, teams, bars):
    """
    Build and return a complete schedule for *season*.

    Args:
        season  : Season model instance (needs .start_date, .frequency,
                  .blackout_dates)
        teams   : list of Team model instances participating this season
        bars    : list of Bar model instances whose teams are in the season

    Returns:
        list of dicts:
            {
                'round_num': int,
                'date':      datetime.date,
                'matches':   [(home_team, away_team, bar_id), ...],
                'bye':       Team | None,
            }
    """
    team_list = list(teams)
    random.shuffle(team_list)          # randomise so regenerate produces variety

    rounds = _round_robin_pairs(team_list)

    assigned_rounds = [
        {
            'matches': _assign_home_away(r['pairs'], bars),
            'bye': r['bye'],
        }
        for r in rounds
    ]

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

def _assign_home_away(pairs, bars):
    """
    For each (team_a, team_b) pair decide which team is *home* (and therefore
    which bar hosts the match).

    Rules, in priority order:
      1. Never exceed a bar's table capacity in a single round.
      2. Prefer assigning home to teams whose bar has not yet hosted a match
         this round (coverage goal: every bar active every week).
      3. Among equally needy bars, prefer the one with more remaining capacity.
      4. Break ties randomly so regeneration produces variety.

    Returns list of (home_team, away_team, bar_id).
    """
    bar_state = {b.id: {'capacity': b.tables, 'load': 0} for b in bars}

    same_bar = [(t1, t2) for t1, t2 in pairs if t1.bar_id == t2.bar_id]
    cross_bar = [(t1, t2) for t1, t2 in pairs if t1.bar_id != t2.bar_id]

    assignments = []

    # --- same-bar pairs: both teams share a bar, so that bar hosts (forced) ---
    for t1, t2 in same_bar:
        bid = t1.bar_id
        bar_state[bid]['load'] += 1
        home, away = (t1, t2) if random.random() < 0.5 else (t2, t1)
        assignments.append((home, away, bid))

    # --- cross-bar pairs: greedy, process most-needy first ---
    def _pair_urgency(pair):
        """Lower = more urgent (process first)."""
        t1, t2 = pair
        return min(bar_state[t1.bar_id]['load'], bar_state[t2.bar_id]['load'])

    cross_bar.sort(key=_pair_urgency)

    for t1, t2 in cross_bar:
        b1id, b2id = t1.bar_id, t2.bar_id
        b1, b2 = bar_state[b1id], bar_state[b2id]

        rem1 = b1['capacity'] - b1['load']
        rem2 = b2['capacity'] - b2['load']

        if rem1 <= 0 and rem2 <= 0:
            # Both bars at capacity — assign to whichever is least over
            home, away = (t1, t2) if rem1 >= rem2 else (t2, t1)
        elif rem1 <= 0:
            home, away = t2, t1      # bar1 full, must use bar2
        elif rem2 <= 0:
            home, away = t1, t2      # bar2 full, must use bar1
        elif b1['load'] == 0 and b2['load'] > 0:
            home, away = t1, t2      # bar1 hasn't had a match yet
        elif b2['load'] == 0 and b1['load'] > 0:
            home, away = t2, t1      # bar2 hasn't had a match yet
        elif rem1 > rem2:
            home, away = t1, t2
        elif rem2 > rem1:
            home, away = t2, t1
        else:
            home, away = (t1, t2) if random.random() < 0.5 else (t2, t1)

        host_bar_id = home.bar_id
        bar_state[host_bar_id]['load'] += 1
        assignments.append((home, away, host_bar_id))

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
