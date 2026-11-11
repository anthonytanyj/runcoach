"""Put the plan's sessions on real days, around your life.

The plan is not a fixed list of dates. Each week has sessions in priority order
(race > long > workout > easy). The scheduler places them one at a time, most
important first, on the first day that passes all the rules:

Hard rules (never broken):
  * No run on a day that a life change blocks (trip, illness, busy day).
  * One run each day.
  * A hard session (long, workout, test, race) never sits next to another hard
    session, so there are at least 48 h between them.
  * No more than 2 run days in a row.
  * A session never moves to a day before ``not_before`` (usually today).
  * A session stays in its own week. Lost km are never "made up" later.

Soft rules (relaxed only if nothing else fits):
  * No hard session on the day after a leg-strength session.
  * A run that must move does not go to a leg-strength day or the day after one.

Your own calendar moves come first: if you moved a run in your calendar app,
it stays on the day you chose (unless a life change now blocks that day), and
the other runs are placed around it. If you removed it, it is dropped.

If a session does not fit anywhere, it is dropped (lowest priority goes first,
because the most important sessions are placed first). The race never moves.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from .models import (
    Adjustment,
    Block,
    BlockEvent,
    ChangeKind,
    LifeChange,
    Move,
    Plan,
    Scheduled,
    Session,
    slot_index,
)

ONE_DAY = dt.timedelta(days=1)


@dataclass
class _Board:
    """Run days placed so far (across weeks, so week edges also follow the rules)."""

    runs: dict[dt.date, bool] = field(default_factory=dict)  # date -> is hard

    def has_run(self, d: dt.date) -> bool:
        return d in self.runs

    def hard_near(self, d: dt.date) -> bool:
        return self.runs.get(d - ONE_DAY, False) or self.runs.get(d + ONE_DAY, False)

    def streak_if_added(self, d: dt.date) -> int:
        n = 1
        x = d - ONE_DAY
        while x in self.runs:
            n += 1
            x -= ONE_DAY
        x = d + ONE_DAY
        while x in self.runs:
            n += 1
            x += ONE_DAY
        return n


def _blocking(changes: list[LifeChange], d: dt.date) -> LifeChange | None:
    for c in changes:
        if c.covers(d) and c.blocks_runs:
            return c
    return None


def _treadmill(changes: list[LifeChange], d: dt.date) -> LifeChange | None:
    for c in changes:
        if c.covers(d) and c.kind == ChangeKind.treadmill:
            return c
    return None


def block_events(plan: Plan, week: int, changes: list[LifeChange]) -> list[BlockEvent]:
    start = plan.week_start(week)
    out: list[BlockEvent] = []
    for b in plan.blocks:
        for slot in b.days:
            d = start + dt.timedelta(days=slot_index(slot))
            if b.until and d > b.until:
                continue
            reason = None
            for c in changes:
                if c.covers(d) and c.blocks_runs and not c.lifts_ok:
                    reason = f"{c.kind.value}: {c.note}".rstrip(": ")
                    break
            out.append(BlockEvent(date=d, block=b, skipped_reason=reason))
    return sorted(out, key=lambda e: e.date)


def _leg_days(events: list[BlockEvent]) -> set[dt.date]:
    return {e.date for e in events if e.block.legs and e.skipped_reason is None}


def _candidates(start: dt.date, preferred: dt.date) -> list[dt.date]:
    days = [start + dt.timedelta(days=i) for i in range(7)]
    # Closest day first; on a tie, the later day first (keeps the week's order).
    return sorted(days, key=lambda d: (abs((d - preferred).days), -(d - preferred).days))


def _fits(
    s: Session,
    d: dt.date,
    board: _Board,
    changes: list[LifeChange],
    leg_days: set[dt.date],
    plan: Plan,
    not_before: dt.date | None,
    original: dt.date,
    soft: bool,
) -> bool:
    if d > plan.race.date:
        return False
    if not_before and original >= not_before and d < not_before:
        return False
    if _blocking(changes, d):
        return False
    if board.has_run(d):
        return False
    if s.hard and board.hard_near(d):
        return False
    if board.streak_if_added(d) > 2:
        return False
    if soft and s.hard and (d - ONE_DAY) in leg_days:
        return False
    # When a run must move, try not to put it on a leg day or the day after one.
    if soft and d != original and (d in leg_days or (d - ONE_DAY) in leg_days):
        return False
    return True


def schedule(
    plan: Plan,
    changes: list[LifeChange] | None = None,
    not_before: dt.date | None = None,
    moves: dict[str, Move] | None = None,
) -> list[Scheduled]:
    """Schedule every week of the plan. Returns sessions sorted by date."""
    changes = changes or []
    moves = moves or {}
    board = _Board()
    result: list[Scheduled] = []

    for week in plan.weeks:
        start = plan.week_start(week.n)
        leg_days = _leg_days(block_events(plan, week.n, changes))
        def _key(s: Session) -> str:
            return f"w{week.n}-{s.slot}-{s.type}"

        # Runs the runner moved in their calendar go first, then the rest by priority.
        order = sorted(week.sessions, key=lambda s: (_key(s) not in moves, s.priority, slot_index(s.slot)))
        for s in order:
            preferred = start + dt.timedelta(days=slot_index(s.slot))
            item = Scheduled(date=preferred, week=week.n, session=s, original_date=preferred)

            mv = moves.get(_key(s))
            if mv is not None and s.type != "race":
                if mv.removed:
                    item.status = "dropped"
                    item.adjustments.append(Adjustment(
                        kind="dropped", rule="you_chose",
                        reason="You removed this run from your calendar. Lost km are not made up."))
                    result.append(item)
                    continue
                if not _blocking(changes, mv.date):
                    if s.hard and board.hard_near(mv.date):
                        item.adjustments.append(Adjustment(
                            kind="note", rule="rest_between_hard",
                            reason="This hard run is now next to another hard day. Go gently if your legs feel tired."))
                    board.runs[mv.date] = s.hard
                    item.date = mv.date
                    if mv.date != preferred:
                        item.adjustments.append(Adjustment(
                            kind="moved", rule="you_chose",
                            reason=f"You moved this run in your calendar from {preferred:%a %-d %b}."))
                    result.append(item)
                    continue

            if s.type == "race":
                board.runs[preferred] = True
                block = _blocking(changes, preferred)
                if block:
                    item.adjustments.append(
                        Adjustment(kind="moved", reason=f"Race day is fixed, but you marked it '{block.kind.value}'. Check this.")
                    )
                result.append(item)
                continue

            placed = None
            for soft in (True, False):
                for d in _candidates(start, preferred):
                    if _fits(s, d, board, changes, leg_days, plan, not_before, preferred, soft):
                        placed = d
                        break
                if placed:
                    break

            if placed is None:
                item.status = "dropped"
                why = _blocking(changes, preferred)
                reason = (
                    f"No free day this week ({why.kind.value}{': ' + why.note if why.note else ''})."
                    if why
                    else "No day this week passes the rules (48 h between hard runs, max 2 run days in a row)."
                )
                item.adjustments.append(Adjustment(kind="dropped", reason=reason + " Lost km are not made up."))
                result.append(item)
                continue

            board.runs[placed] = s.hard
            item.date = placed
            if placed != preferred:
                why = _blocking(changes, preferred)
                reason = f"Moved from {preferred:%a %d %b}"
                if why:
                    reason += f" ({why.kind.value}{': ' + why.note if why.note else ''})"
                elif board.has_run(preferred):
                    reason += " (day taken by a more important run)"
                item.adjustments.append(Adjustment(kind="moved", reason=reason + "."))
            tm = _treadmill(changes, placed)
            if tm:
                item.adjustments.append(
                    Adjustment(kind="treadmill", reason=f"Treadmill only{': ' + tm.note if tm.note else ''}. Use the km/h and mph speeds.")
                )
            result.append(item)

    return sorted(result, key=lambda x: (x.date, x.session.priority))


def all_block_events(plan: Plan, changes: list[LifeChange] | None = None) -> list[BlockEvent]:
    changes = changes or []
    out: list[BlockEvent] = []
    for w in plan.weeks:
        out.extend(block_events(plan, w.n, changes))
    return out


__all__ = ["schedule", "block_events", "all_block_events", "Block"]
