"""
tests/test_regenerate.py — season_regenerate and season_regenerate_partial
happy paths, and _reconstruct_state's history/streak seeding.
"""

from itertools import combinations

from app import db
from app.main.routes import _reconstruct_state
from app.models import Match, Season, Team


# ---------------------------------------------------------------------------
# Full regenerate
# ---------------------------------------------------------------------------

def test_regenerate_replaces_matches_but_preserves_round_robin_shape(
        app, admin_client, create_season, sample_league):
    season_id = create_season('Regen Season', sample_league['team_ids'], num_weeks=3)

    with app.app_context():
        before_ids = sorted(m.id for m in Match.query.filter_by(season_id=season_id).all())

    response = admin_client.post('/seasons/{0}/regenerate'.format(season_id), data={})
    assert response.status_code == 302

    with app.app_context():
        matches = Match.query.filter_by(season_id=season_id).all()
        assert len(matches) == 6
        assert sorted({m.round_num for m in matches}) == [1, 2, 3]
        # NOTE: we deliberately do not assert before_ids/after_ids differ.
        # SQLite tables without AUTOINCREMENT recycle freed rowids (see
        # CLAUDE.md) — deleting all 6 rows for this (only) season and
        # reinserting 6 fresh ones reliably lands back on the same 6 ids in
        # this env, so id identity/difference isn't a meaningful signal
        # here. `before_ids` is kept only for readability/documentation of
        # that fact.
        assert before_ids  # sanity: matches existed before regenerating
        # Still a complete single-cycle round robin over the same 4 teams.
        pairs = [frozenset({m.home_team_id, m.away_team_id}) for m in matches]
        expected_pairs = {frozenset(p) for p in combinations(sample_league['team_ids'], 2)}
        assert set(pairs) == expected_pairs
        assert len(pairs) == len(set(pairs))


def test_archived_season_cannot_be_regenerated(app, admin_client, create_season, sample_league):
    season_id = create_season('Archived Regen Season', sample_league['team_ids'], num_weeks=3)
    archive_response = admin_client.post('/seasons/{0}/archive'.format(season_id), data={})
    assert archive_response.status_code == 302

    with app.app_context():
        before_ids = sorted(m.id for m in Match.query.filter_by(season_id=season_id).all())

    response = admin_client.post(
        '/seasons/{0}/regenerate'.format(season_id), data={}, follow_redirects=True)
    assert response.status_code == 200
    assert b'Archived seasons cannot be regenerated.' in response.data

    with app.app_context():
        after_ids = sorted(m.id for m in Match.query.filter_by(season_id=season_id).all())
    assert before_ids == after_ids  # untouched


# ---------------------------------------------------------------------------
# Partial regenerate — frozen rounds preserved, tail regenerated, state
# (matchup history / streaks) carries across the freeze boundary.
# ---------------------------------------------------------------------------

def test_regenerate_partial_preserves_frozen_rounds_and_continues_alternation(
        app, admin_client, create_season, sample_league):
    # Two teams sharing one bar -> every round is the same pair, decided by
    # the algorithm's same-bar branch. Under strict alternation each team's
    # streak never exceeds 1, so streak-forcing never kicks in — only
    # matchup-history flip governs after round 1. That makes home/away
    # strictly alternate round to round regardless of which team the
    # round-1 tie-break happens to pick, with no seed control needed.
    two_team_ids = sample_league['team_ids'][:2]  # Sharks, Hustlers (bar_a)
    season_id = create_season('Partial Regen Season', two_team_ids, num_weeks=8)

    with app.app_context():
        original = {
            m.round_num: (m.id, m.home_team_id, m.away_team_id, m.date)
            for m in Match.query.filter_by(season_id=season_id).all()
        }
    assert sorted(original) == list(range(1, 9))
    homes = [original[r][1] for r in range(1, 9)]
    assert all(homes[i] != homes[i + 1] for i in range(len(homes) - 1)), \
        'test setup assumption violated: matches should alternate home/away'

    response = admin_client.post(
        '/seasons/{0}/regenerate-partial'.format(season_id),
        data={'freeze_through_round': '4'})
    assert response.status_code == 302

    with app.app_context():
        after = {
            m.round_num: (m.id, m.home_team_id, m.away_team_id, m.date)
            for m in Match.query.filter_by(season_id=season_id).all()
        }

    # Rounds 1-4 (frozen): byte-for-byte untouched, same row — including id,
    # which is a valid check in this direction (an unchanged row keeps its
    # primary key regardless of SQLite's rowid-recycling behavior elsewhere).
    for r in range(1, 5):
        assert after[r] == original[r], 'frozen round {0} was modified'.format(r)

    # Rounds 5-8 still exist with the right shape. (We don't assert
    # after[r][0] != original[r][0] here: SQLite tables without
    # AUTOINCREMENT recycle freed rowids — see CLAUDE.md — and in this
    # scenario the freed ids for rounds 5-8 are exactly the ids the new
    # rows land back on, so id (in)equality isn't a meaningful "was this
    # regenerated" signal. See
    # test_regenerate_partial_actually_reruns_the_scheduler_for_the_tail
    # below for a seed-based proof that the tail is genuinely recomputed.)
    assert sorted(after) == list(range(1, 9))
    for r in range(5, 9):
        # The weekly date cadence is reproduced exactly (no blackouts to
        # perturb it).
        assert after[r][3] == original[r][3], 'round {0} date drifted'.format(r)

    # Continuity across the freeze boundary: round 5's home team differs
    # from round 4's (the matchup-history flip carries over rather than
    # restarting cold), and alternation continues through the regenerated
    # tail.
    assert after[5][1] != after[4][1]
    regen_homes = [after[r][1] for r in range(4, 9)]
    assert all(regen_homes[i] != regen_homes[i + 1] for i in range(len(regen_homes) - 1))


def test_regenerate_partial_actually_reruns_the_scheduler_for_the_tail(
        app, admin_client, create_season, sample_league):
    """
    Positive proof that regenerate-partial genuinely re-invokes the
    scheduler for the tail rather than being a no-op that happens to leave
    the old rows in place (which, per the id-recycling note above, could
    otherwise go unnoticed by id alone): two identically-set-up seasons,
    regenerated with two different global random seeds, must end up with
    different tail assignments.
    """
    import random

    season_ids = []
    for name in ('Reroll Season A', 'Reroll Season B'):
        random.seed(1)  # identical starting shuffle for both seasons' initial creation
        season_ids.append(create_season(name, sample_league['team_ids'], num_weeks=6))

    tails = []
    for season_id, seed in zip(season_ids, (11, 22)):
        random.seed(seed)
        response = admin_client.post(
            '/seasons/{0}/regenerate-partial'.format(season_id),
            data={'freeze_through_round': '3'})
        assert response.status_code == 302
        with app.app_context():
            tail = sorted(
                (m.round_num, m.home_team_id, m.away_team_id)
                for m in Match.query.filter_by(season_id=season_id).all()
                if m.round_num > 3
            )
        tails.append(tail)

    assert tails[0] != tails[1], (
        'two different random seeds produced an identical regenerated tail '
        '— regenerate-partial may not actually be re-running the scheduler')


def test_regenerate_partial_rejects_freeze_round_at_or_above_total(
        app, admin_client, create_season, sample_league):
    season_id = create_season('Bad Freeze Season', sample_league['team_ids'], num_weeks=3)
    with app.app_context():
        before = sorted(m.id for m in Match.query.filter_by(season_id=season_id).all())

    response = admin_client.post(
        '/seasons/{0}/regenerate-partial'.format(season_id),
        data={'freeze_through_round': '3'},  # total_rounds == 3, must be strictly less
        follow_redirects=True)
    assert response.status_code == 200
    assert b'Freeze round must be less than the total number of rounds.' in response.data

    with app.app_context():
        after = sorted(m.id for m in Match.query.filter_by(season_id=season_id).all())
    assert before == after


def test_archived_season_cannot_be_partially_regenerated(
        app, admin_client, create_season, sample_league):
    season_id = create_season('Archived Partial Season', sample_league['team_ids'], num_weeks=3)
    admin_client.post('/seasons/{0}/archive'.format(season_id), data={})

    response = admin_client.post(
        '/seasons/{0}/regenerate-partial'.format(season_id),
        data={'freeze_through_round': '1'}, follow_redirects=True)
    assert response.status_code == 200
    assert b'Only active seasons can be partially regenerated.' in response.data


# ---------------------------------------------------------------------------
# _reconstruct_state — direct unit test of the history/streak replay
# ---------------------------------------------------------------------------

def test_reconstruct_state_replays_frozen_matches_into_history_and_streaks(
        app, admin_client, create_season, sample_league):
    season_id = create_season('Reconstruct Season', sample_league['team_ids'], num_weeks=3)

    with app.app_context():
        season = db.session.get(Season, season_id)
        matches = sorted(Match.query.filter_by(season_id=season_id).all(),
                         key=lambda m: m.round_num)

        # Mirror generate_schedule()'s own streak recurrence independently,
        # from the persisted match rows, as the expected result.
        expected_history = {}
        expected_streaks = {}
        for m in matches:
            expected_history[frozenset({m.home_team_id, m.away_team_id})] = m.home_team_id
            s = expected_streaks.get(m.home_team_id, 0)
            expected_streaks[m.home_team_id] = s + 1 if s > 0 else 1
            s = expected_streaks.get(m.away_team_id, 0)
            expected_streaks[m.away_team_id] = s - 1 if s < 0 else -1

        history, streaks, frozen_dates = _reconstruct_state(season, freeze_through_round=3)

        assert history == expected_history
        assert streaks == expected_streaks
        assert frozen_dates == {m.date for m in matches}


def test_reconstruct_state_only_replays_rounds_up_to_the_freeze_point(
        app, admin_client, create_season, sample_league):
    # 5 teams, one single round-robin cycle (5 rounds, no re-shuffle) -> every
    # pair meets exactly once across the whole season, so rounds 4-5
    # necessarily involve pairs that never appear in rounds 1-3. Freezing at
    # round 3 must leave those tail-only pairs out of the reconstructed
    # history entirely, even though the rows already exist in the database
    # when _reconstruct_state runs (it's called before the tail is deleted).
    with app.app_context():
        wildcards = Team(name='Wildcards', bar_id=sample_league['bar_ids'][0])
        db.session.add(wildcards)
        db.session.commit()
        team_ids = sample_league['team_ids'] + [wildcards.id]

    season_id = create_season('Reconstruct Tail Season', team_ids, num_weeks=5)

    with app.app_context():
        season = db.session.get(Season, season_id)
        all_matches = Match.query.filter_by(season_id=season_id).all()
        frozen_matches = [m for m in all_matches if m.round_num <= 3]
        tail_matches = [m for m in all_matches if m.round_num > 3]
        assert tail_matches, 'test setup assumption violated: expected rounds beyond 3'

        frozen_pairs = {frozenset({m.home_team_id, m.away_team_id}) for m in frozen_matches}
        tail_pairs = {frozenset({m.home_team_id, m.away_team_id}) for m in tail_matches}
        assert tail_pairs, 'test setup assumption violated: expected tail matches'
        assert frozen_pairs.isdisjoint(tail_pairs), (
            'test setup assumption violated: a single cycle should never repeat a pair')

        history, streaks, frozen_dates = _reconstruct_state(season, freeze_through_round=3)

        assert frozen_dates == {m.date for m in frozen_matches}
        # Every pair that met in the frozen portion is represented...
        for m in frozen_matches:
            assert history[frozenset({m.home_team_id, m.away_team_id})] == m.home_team_id
        # ...and no tail-only pair leaked into the reconstructed history.
        for pair in tail_pairs:
            assert pair not in history
