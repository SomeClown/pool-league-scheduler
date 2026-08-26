"""
tests/test_scheduler_algorithm.py — Behavior tests for the round-robin scheduler.

Tests go through the public entry point, generate_schedule(). The algorithm
only reads plain attributes off its inputs (team.id / team.bar_id,
bar.id / bar.tables_in_use / bar.tables, season.start_date / .frequency /
.blackout_dates / .bar_caps, cap.bar_id / cap.tables_used), so lightweight
stand-in objects are used instead of ORM models — no database needed, which
keeps these tests fast and hermetic.

The random module is seeded per test for determinism; capacity and streak
invariants are additionally checked across several seeds.
"""

import random
from datetime import date, timedelta
from itertools import combinations

from app.scheduler.algorithm import MAX_HOME_AWAY_STREAK, generate_schedule


# ---------------------------------------------------------------------------
# Lightweight stand-ins for the ORM models
# ---------------------------------------------------------------------------

class FakeTeam:
    def __init__(self, team_id, bar_id):
        self.id = team_id
        self.bar_id = bar_id

    def __repr__(self):
        return '<FakeTeam {0}>'.format(self.id)


class FakeBar:
    def __init__(self, bar_id, tables_in_use):
        self.id = bar_id
        self.tables_in_use = tables_in_use
        self.tables = tables_in_use


class FakeCap:
    def __init__(self, bar_id, tables_used):
        self.bar_id = bar_id
        self.tables_used = tables_used


class FakeBlackout:
    def __init__(self, blackout_date):
        self.date = blackout_date


class FakeSeason:
    def __init__(self, start_date=date(2026, 9, 1), frequency='weekly',
                 blackout_dates=None, bar_caps=None):
        self.start_date = start_date
        self.frequency = frequency
        self.blackout_dates = blackout_dates or []
        self.bar_caps = bar_caps or []


def make_league(teams_per_bar, tables_per_bar):
    """
    Build bars/teams from a list of per-bar team counts.

    teams_per_bar=[2, 2, 3] creates 3 bars holding 2, 2 and 3 teams.
    Returns (teams, bars).
    """
    teams, bars = [], []
    next_team_id = 1
    for bar_index, count in enumerate(teams_per_bar):
        bar_id = bar_index + 1
        bars.append(FakeBar(bar_id, tables_per_bar))
        for _ in range(count):
            teams.append(FakeTeam(next_team_id, bar_id))
            next_team_id += 1
    return teams, bars


def all_pairs(teams):
    return {frozenset({a.id, b.id}) for a, b in combinations(teams, 2)}


def pairs_in_rounds(rounds):
    """Multiset (list) of frozenset team-id pairs across the given rounds."""
    result = []
    for rnd in rounds:
        for home, away, _bar_id in rnd['matches']:
            result.append(frozenset({home.id, away.id}))
    return result


# ---------------------------------------------------------------------------
# Round-robin correctness
# ---------------------------------------------------------------------------

def test_even_team_count_single_cycle_every_pair_meets_once():
    random.seed(1)
    teams, bars = make_league([2, 2, 2], tables_per_bar=5)
    schedule = generate_schedule(FakeSeason(), teams, bars)  # num_rounds=None → one cycle

    assert len(schedule) == 5  # N-1 rounds for N=6 teams

    played = pairs_in_rounds(schedule)
    assert sorted(played, key=sorted) == sorted(all_pairs(teams), key=sorted)
    assert len(played) == len(set(played)), 'a pair met more than once in one cycle'

    for rnd in schedule:
        assert rnd['bye'] is None, 'even team count must not produce byes'
        ids_this_round = [t.id for h, a, _ in rnd['matches'] for t in (h, a)]
        assert sorted(ids_this_round) == sorted(t.id for t in teams), \
            'every team must play exactly once per round'


def test_odd_team_count_rotating_bye_and_full_coverage():
    random.seed(2)
    teams, bars = make_league([3, 2, 2], tables_per_bar=5)  # 7 teams
    schedule = generate_schedule(FakeSeason(), teams, bars)

    assert len(schedule) == 7  # N rounds for odd N=7

    bye_ids = []
    for rnd in schedule:
        assert rnd['bye'] is not None, 'odd team count: every round needs a bye'
        assert len(rnd['matches']) == 3
        bye_ids.append(rnd['bye'].id)
        playing = [t.id for h, a, _ in rnd['matches'] for t in (h, a)]
        assert rnd['bye'].id not in playing
        assert sorted(playing + [rnd['bye'].id]) == sorted(t.id for t in teams)

    assert sorted(bye_ids) == sorted(t.id for t in teams), \
        'each team must sit out exactly once per cycle'

    played = pairs_in_rounds(schedule)
    assert sorted(played, key=sorted) == sorted(all_pairs(teams), key=sorted)


def test_multi_cycle_each_cycle_is_a_complete_round_robin():
    random.seed(3)
    teams, bars = make_league([2, 2], tables_per_bar=5)  # 4 teams → 3-round cycle
    schedule = generate_schedule(FakeSeason(), teams, bars, num_rounds=6)

    assert len(schedule) == 6
    expected = all_pairs(teams)
    first_cycle = pairs_in_rounds(schedule[:3])
    second_cycle = pairs_in_rounds(schedule[3:])
    assert set(first_cycle) == expected and len(first_cycle) == len(expected)
    assert set(second_cycle) == expected and len(second_cycle) == len(expected)


def test_round_numbers_are_sequential_from_one():
    random.seed(4)
    teams, bars = make_league([2, 2], tables_per_bar=5)
    schedule = generate_schedule(FakeSeason(), teams, bars, num_rounds=5)
    assert [rnd['round_num'] for rnd in schedule] == [1, 2, 3, 4, 5]


def test_fewer_than_two_teams_raises_value_error():
    teams, bars = make_league([1], tables_per_bar=1)
    try:
        generate_schedule(FakeSeason(), teams, bars)
    except ValueError:
        pass
    else:
        raise AssertionError('expected ValueError for a 1-team season')


# ---------------------------------------------------------------------------
# Table capacity (hard cap)
# ---------------------------------------------------------------------------

def test_bar_capacity_hard_cap_never_exceeded():
    # 3 bars, 2 teams each, 1 table each. Every round needs exactly one home
    # match per bar — the tightest feasible configuration.
    for seed in range(10):
        random.seed(seed)
        teams, bars = make_league([2, 2, 2], tables_per_bar=1)
        schedule = generate_schedule(FakeSeason(), teams, bars, num_rounds=20)

        for rnd in schedule:
            load = {}
            for home, away, bar_id in rnd['matches']:
                assert bar_id == home.bar_id, 'match must be hosted at the home team bar'
                load[bar_id] = load.get(bar_id, 0) + 1
            for bar in bars:
                assert load.get(bar.id, 0) <= bar.tables_in_use, \
                    'seed {0}, round {1}: bar {2} over capacity'.format(
                        seed, rnd['round_num'], bar.id)


def test_per_season_bar_cap_overrides_standing_limit():
    # Bar 1 physically allows 3 tables but the season caps it at 1.
    for seed in range(10):
        random.seed(seed)
        teams, bars = make_league([3, 3], tables_per_bar=3)
        season = FakeSeason(bar_caps=[FakeCap(bar_id=1, tables_used=1),
                                      FakeCap(bar_id=2, tables_used=2)])
        schedule = generate_schedule(season, teams, bars, num_rounds=20)

        for rnd in schedule:
            load = {}
            for home, _away, bar_id in rnd['matches']:
                load[bar_id] = load.get(bar_id, 0) + 1
            assert load.get(1, 0) <= 1, \
                'seed {0}, round {1}: season cap on bar 1 exceeded'.format(
                    seed, rnd['round_num'])
            assert load.get(2, 0) <= 2


# ---------------------------------------------------------------------------
# Home/away streak cap
# ---------------------------------------------------------------------------

def test_home_away_streak_cap_respected():
    # Ample capacity everywhere, so capacity never forces a streak violation.
    #
    # Documented edge case (see _streak_forced): when the two teams in a match
    # are BOTH at the cap with an EQUAL signed streak, one of them must extend
    # past the cap — that is unavoidable for the round's fixed pairing and is
    # explicitly allowed. Any other violation is a bug.
    for seed in range(10):
        random.seed(seed)
        teams, bars = make_league([2, 2, 2], tables_per_bar=10)
        schedule = generate_schedule(FakeSeason(), teams, bars, num_rounds=40)

        streaks = {t.id: 0 for t in teams}
        for rnd in schedule:
            pre = dict(streaks)
            for home, away, _bar_id in rnd['matches']:
                s = streaks[home.id]
                streaks[home.id] = s + 1 if s > 0 else 1
                s = streaks[away.id]
                streaks[away.id] = s - 1 if s < 0 else -1

                for team, opponent in ((home, away), (away, home)):
                    if abs(streaks[team.id]) > MAX_HOME_AWAY_STREAK:
                        assert pre[team.id] == pre[opponent.id], (
                            'seed {0}, round {1}: team {2} streak reached {3} '
                            'without the unavoidable equal-streak pairing '
                            '(pre-round streaks: {4} vs {5})'.format(
                                seed, rnd['round_num'], team.id,
                                streaks[team.id], pre[team.id],
                                pre[opponent.id]))


def test_byes_do_not_reset_streaks():
    # With an odd team count each team sits out once per cycle; the streak
    # bookkeeping must carry across the bye rather than reset. We verify the
    # cap still holds when computed the way the algorithm defines it
    # (consecutive games played, byes transparent).
    for seed in range(5):
        random.seed(seed)
        teams, bars = make_league([3, 2], tables_per_bar=10)  # 5 teams, byes
        schedule = generate_schedule(FakeSeason(), teams, bars, num_rounds=30)

        streaks = {t.id: 0 for t in teams}
        for rnd in schedule:
            pre = dict(streaks)
            for home, away, _bar_id in rnd['matches']:
                s = streaks[home.id]
                streaks[home.id] = s + 1 if s > 0 else 1
                s = streaks[away.id]
                streaks[away.id] = s - 1 if s < 0 else -1
                for team, opponent in ((home, away), (away, home)):
                    if abs(streaks[team.id]) > MAX_HOME_AWAY_STREAK:
                        assert pre[team.id] == pre[opponent.id], (
                            'seed {0}, round {1}: team {2} exceeded the streak '
                            'cap outside the equal-streak edge case'.format(
                                seed, rnd['round_num'], team.id))


# ---------------------------------------------------------------------------
# Date mapping
# ---------------------------------------------------------------------------

def test_weekly_dates_skip_blackouts():
    random.seed(5)
    start = date(2026, 9, 1)
    blackout = start + timedelta(weeks=1)
    season = FakeSeason(start_date=start, frequency='weekly',
                        blackout_dates=[FakeBlackout(blackout)])
    teams, bars = make_league([2, 2], tables_per_bar=5)
    schedule = generate_schedule(season, teams, bars, num_rounds=3)

    dates = [rnd['date'] for rnd in schedule]
    assert dates == [start, start + timedelta(weeks=2), start + timedelta(weeks=3)]
    assert blackout not in dates


def test_biweekly_frequency_steps_two_weeks():
    random.seed(6)
    start = date(2026, 9, 1)
    season = FakeSeason(start_date=start, frequency='biweekly')
    teams, bars = make_league([2, 2], tables_per_bar=5)
    schedule = generate_schedule(season, teams, bars, num_rounds=3)

    assert [rnd['date'] for rnd in schedule] == [
        start, start + timedelta(weeks=2), start + timedelta(weeks=4)]
