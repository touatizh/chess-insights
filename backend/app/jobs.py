"""Incremental report job (§6.5).

The RQ worker calls :func:`run_report_job` with a report id + username. The job:

1. fetches the latest 30 games from Lichess (progress 0→20),
2. dedupes against the DB by ``lichess_id`` per player and inserts only new games,
3. analyzes **new** games only, game-by-game (20→90, ``Report.progress`` bumped
   after each; 0 new games skips straight to aggregation),
4. aggregates from the DB's latest 30 games, reusing stored MoveEvals (90→100,
   status ``done``).

Any exception marks the report ``failed`` with a user-safe message.
"""

from __future__ import annotations

import chess
from sqlmodel import Session

from app.analysis import engine
from app.analysis.report import GameInput, MoveEvalInput, ReportPayload, build_report
from app.db import (
    fail_report,
    get_or_create_player,
    insert_move_evals,
    insert_new_games,
    latest_games,
    move_evals_for_games,
    session_scope,
    set_report_payload,
    update_report,
)
from app.lichess import LichessError, LichessRateLimitError, LichessUserNotFoundError, fetch_games
from app.models import Game

# Progress window boundaries per §6.5: fetch 0→20, analyze 20→90, aggregate 90→100.
_FETCH_PROGRESS = 20
_ANALYZE_START_PROGRESS = 20
_AGGREGATE_START_PROGRESS = 90

_MAX_GAMES = 30


def run_report_job(report_id: int, username: str) -> None:
    """Run the full incremental analysis pipeline for one report (§6.5)."""
    with session_scope() as session:
        try:
            player_id, _ = get_or_create_player(username, session)
            update_report(session, report_id, status="fetching", progress=0)

            # 1. Fetch latest 30 games.
            games = fetch_games(username, max_games=_MAX_GAMES)
            update_report(session, report_id, progress=_FETCH_PROGRESS)

            # 2. Dedupe + insert only new games (per player).
            new_games = insert_new_games(session, player_id, games)

            # 3. Analyze new games only. _analyze_new_games sets status=analyzing
            # plus total_new/analyzed_new so the frontend can show "N/M".
            if new_games:
                _analyze_new_games(session, report_id, player_id)
            else:
                update_report(session, report_id, progress=_AGGREGATE_START_PROGRESS)

            # 4. Aggregate from DB (latest 30, reusing stored MoveEvals).
            payload = _aggregate(session, player_id, username)
            set_report_payload(session, report_id, payload)

        except Exception as exc:  # noqa: BLE001 - any failure → failed report
            fail_report(session, report_id, _user_safe_error(exc))
            raise


def _analyze_new_games(session: Session, report_id: int, player_id: int) -> None:
    """Analyze every unanalyzed game in the latest 30, progress 20→90.

    Progress is scaled by the actual number of unanalyzed games (not just those
    inserted this run) so a crash-and-retry that leaves games unanalyzed from a
    prior run still ends at the 90 boundary rather than overshooting it.
    """
    games = latest_games(session, player_id, limit=_MAX_GAMES)
    pending = [game for game in games if not game.analyzed]
    total = len(pending)
    span = _AGGREGATE_START_PROGRESS - _ANALYZE_START_PROGRESS
    # Persist the denominator up front so the frontend's "Analyzing N/M" label
    # has something to render from the first poll onward.
    update_report(session, report_id, status="analyzing", total_new=total, analyzed_new=0)
    with engine.open_engine() as eng:
        for analyzed_count, game in enumerate(pending, start=1):
            try:
                _analyze_game(session, eng, game)
            except engine.MoveParseError:
                # One malformed/ambiguous game must not sink the whole report
                # (§6.1 sparse-games philosophy). Mark it analyzed with no evals
                # so incremental re-runs never retry-and-refail it, then move on.
                # It contributes nothing to aggregation, exactly like a quiet game.
                game.analyzed = True
                session.add(game)
                session.commit()
            update_report(
                session,
                report_id,
                progress=_ANALYZE_START_PROGRESS + (analyzed_count * span // total),
                analyzed_new=analyzed_count,
            )


def _analyze_game(session: Session, eng: engine.Analyzer, game: Game) -> None:
    """Analyze a single new game and persist its MoveEval rows (§4, §6.5)."""
    subject_color = chess.WHITE if game.color == "white" else chess.BLACK
    analyses = engine.analyze_game(eng, game.moves, subject_color, depth=12)
    insert_move_evals(session, game, analyses)
    # Mark the game as analyzed so future incremental runs skip it.
    game.analyzed = True
    session.commit()


def _aggregate(session: Session, player_id: int, username: str) -> ReportPayload:
    """Build the §6.4 payload from the DB's latest 30 games."""
    games = latest_games(session, player_id, limit=_MAX_GAMES)
    if not games:
        return build_report(username, [])

    games_with_id: list[Game] = []
    for game in games:
        if game.id is not None:
            games_with_id.append(game)
    game_ids = [game.id for game in games_with_id if game.id is not None]
    evals_by_game = move_evals_for_games(session, game_ids)
    game_inputs: list[GameInput] = []
    for game in games_with_id:
        game_id = game.id
        assert game_id is not None
        game_evals = evals_by_game.get(game_id, [])
        game_inputs.append(
            GameInput(
                color=game.color,
                result=game.result,
                played_at=game.played_at,
                opening_name=game.opening_name,
                opening_eco=game.opening_eco,
                move_evals=tuple(
                    MoveEvalInput(
                        ply=ev.ply,
                        cp_loss=ev.cp_loss,
                        phase=ev.phase,
                        severity=ev.severity,
                    )
                    for ev in game_evals
                ),
            )
        )
    return build_report(username, game_inputs)


def _user_safe_error(exc: Exception) -> str:
    """Map an exception to a user-safe message."""
    if isinstance(exc, engine.MoveParseError):
        # Parsing error indicates a malformed or ambiguous move string; the user
        # didn't make the game invalid, so we skip that game and try the next.
        if exc.args:
            return exc.args[0]
        return "A move in one of your games could not be parsed; trying the next game."
    if isinstance(exc, LichessUserNotFoundError):
        return f"Lichess user '{exc.args[0]}' not found."
    if isinstance(exc, LichessRateLimitError):
        return "Lichess rate limited; try again in a few minutes."
    if isinstance(exc, LichessError):
        return "Lichess request failed; try again later."
    return "Analysis failed; try again later."
