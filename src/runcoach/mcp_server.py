"""runcoach as an MCP server, so an AI assistant can be your ambient coach.

The assistant brings the triggers (a morning task), the chat, and its own
connectors (Garmin, calendar, email). runcoach brings the plan, the rules, and
the scheduler. The AI never edits the plan directly: it turns what it sees
into a life change or body data, and runcoach computes the new plan.

Run:  RUNCOACH_PLAN=plans/sample-half-marathon.yaml runcoach mcp
"""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from . import calendar_out
from .engine import Coach
from .models import Activity, ChangeKind, DayMetrics, LifeChange, Move, PainReport
from .rules import RULES
from .render import description, one_line

INSTRUCTIONS = """\
runcoach is the runner's training plan and rules engine. Use it like this:

MORNING CHECK (daily, about 6:00, or when asked "what's my run today?"):
1. Get body data for today and yesterday (readiness, resting HR, HRV, sleep) and
   recent activities from the runner's watch connector, if you have one. Send them
   with log_metrics and log_activity. (Or call sync_garmin if it is set up.)
2. Read the runner's main calendar for the next 7 days. If a trip, a late meeting,
   or an all-day event blocks a run day, call preview_change.
3. If preview_change says needs_ok=false, call apply_change and tell the runner in
   one line. If needs_ok=true, show the before/after and ask first.
4. Calendar, two-way. If sync_google_calendar is set up, call it: it reads the runs
   the runner moved or deleted in the calendar and keeps their choice, then writes
   the plan. Otherwise: read the runner's "Training" calendar first. For each runcoach
   event (same id as in calendar_events) that is now on another day or time, call
   calendar_moved with its key and the new date and time. Then call calendar_events
   for the next 14 days and write them (replace events with the same id).
5. Send one short note only if today's run changed.

HOW TO TALK TO THE RUNNER: friendly, plain words, like a good coach. Never send raw
numbers such as "5.1 h sleep" or "HRV 42"; say what they mean compared with the
runner's normal ("you slept about an hour less than usual"). Pace is fine, but pair
it with how it should feel ("easy, you can chat"). Give pace per km and per mile.

PAIN AND QUESTIONS: if a run has a question ("ask"), ask the runner how they feel and
send the answer with check_in. If the runner mentions pain, call log_pain. Never
diagnose. Pain that changes how they run means no run, and pain that lasts several
days means "see a physio or doctor".

A run the runner moved in their calendar stays where they put it. Tell them in one
line if the move puts two hard days next to each other. undo_last_change or
back_to_plan puts it back.

Never change the plan any other way. Illness: no running until 24 h without symptoms.
"""


def _coach() -> Coach:
    plan = os.environ.get("RUNCOACH_PLAN")
    if not plan:
        default = Path.home() / ".runcoach" / "plan.yaml"
        plan = str(default)
    return Coach.from_files(plan, os.environ.get("RUNCOACH_STATE"))


def _d(s: str | None) -> dt.date:
    return dt.date.fromisoformat(s) if s else dt.date.today()


server = MCPServer(name="runcoach", instructions=INSTRUCTIONS)


@server.tool()
def today(date: str | None = None) -> dict:
    """Today's run (or rest), with pace, treadmill speed, HR, why it changed, and the next 3 runs.

    date: YYYY-MM-DD, default today.
    """
    c = _coach()
    t = c.today(_d(date))
    return {
        "date": str(t["date"]),
        "week": t["week"],
        "recovery": {k: t["recovery"][k] for k in ("state", "headline", "lines")},
        "runs": [{"line": one_line(c.plan, r), "details": description(c.plan, r), "rest": r.status == "rest",
                  "question": next((a.reason for a in r.adjustments if a.kind == "ask"), None)} for r in t["runs"]],
        "rest_day": not t["runs"],
        "lifts": [b.block.name for b in t["lifts"]],
        "next": [one_line(c.plan, r) for r in t["next"]],
        "body_data": t["metrics"].model_dump(mode="json") if t["metrics"] else None,
    }


@server.tool()
def week(n: int | None = None, date: str | None = None) -> dict:
    """All sessions in one plan week (default: this week), with any moves, drops, and easy versions."""
    c = _coach()
    day = _d(date)
    n = n or c.plan.week_of(day) or 1
    items = c.week(n, day)
    return {
        "week": n,
        "start": str(c.plan.week_start(n)),
        "summary": c.week_summary(n, day),
        "note": next((w.note for w in c.plan.weeks if w.n == n), None),
        "sessions": [
            {"line": one_line(c.plan, s), "status": s.status, "why": [a.reason for a in s.adjustments]}
            for s in items
        ],
    }


def _change(start: str, end: str | None, kind: str, note: str, lifts_ok: bool) -> LifeChange:
    return LifeChange(start=_d(start), end=_d(end or start), kind=ChangeKind(kind), note=note, lifts_ok=lifts_ok)


@server.tool()
def preview_change(start: str, end: str | None = None, kind: str = "away", note: str = "", lifts_ok: bool = False) -> dict:
    """Show what a life change does to the plan, without saving it.

    kind: away (no runs), busy (one full day), treadmill (runs only on a treadmill), ill.
    lifts_ok: true if strength sessions still happen (for example a hotel gym).
    Returns before/after lines and needs_ok (true = ask the runner before you apply it).
    """
    c = _coach()
    diff = c.preview(_change(start, end, kind, note, lifts_ok))
    return {"changes": diff.summary(c.plan), "needs_ok": diff.needs_ok}


@server.tool()
def apply_change(start: str, end: str | None = None, kind: str = "away", note: str = "", lifts_ok: bool = False) -> dict:
    """Save a life change and re-plan. Only call this after preview_change, and after the runner says OK if needs_ok was true."""
    c = _coach()
    diff = c.apply(_change(start, end, kind, note, lifts_ok))
    return {"applied": diff.summary(c.plan)}


@server.tool()
def undo_last_change() -> dict:
    """Undo the last life change."""
    entry = _coach().undo()
    return {"undone": entry.summary if entry else None}


@server.tool()
def log_metrics(
    date: str,
    readiness: float | None = None,
    resting_hr: float | None = None,
    hrv: float | None = None,
    sleep_hours: float | None = None,
) -> dict:
    """Save body data for one day (from the watch). The rules use it to make runs easier when needed."""
    c = _coach()
    c.store.upsert_metrics(
        [DayMetrics(date=_d(date), readiness=readiness, rhr=resting_hr, hrv=hrv, sleep_hours=sleep_hours)]
    )
    return {"saved": date}


@server.tool()
def log_activity(
    date: str,
    type: str,
    km: float | None = None,
    minutes: float | None = None,
    ascent_m: float | None = None,
    avg_hr: float | None = None,
    name: str | None = None,
) -> dict:
    """Save a finished activity: run, hiking, surfing, strength_training, cycling, ... A run marks that day's session as done."""
    n = _coach().store.add_activities(
        [Activity(date=_d(date), type=type, km=km, minutes=minutes, ascent_m=ascent_m, avg_hr=avg_hr, name=name)]
    )
    return {"added": n}


@server.tool()
def log_feeling(date: str, effort: int | None = None, legs: str | None = None, note: str | None = None) -> dict:
    """Save how a day felt. effort: 1-10. legs: fresh, ok, or heavy. Heavy legs or effort 8+ makes the next day easier."""
    _coach().store.set_feeling(_d(date), effort, legs, note)
    return {"saved": date}


@server.tool()
def check_in(date: str | None = None, feel: str = "fine", legs: str | None = None, effort: int | None = None) -> dict:
    """Save the runner's answer to "How do you feel today?". feel: fine or tired. legs: fresh, ok, heavy. effort: 1-10 for a finished run."""
    c = _coach()
    d = _d(date)
    c.store.set_feeling(d, effort=effort, legs=legs, ready=feel if feel in ("fine", "tired") else None)
    return today(str(d))


@server.tool()
def log_pain(date: str | None = None, where: str = "", score: int = 0, changed_stride: bool = False) -> dict:
    """Save a pain check. score 0-10 (0 = no pain). changed_stride: true if the pain changes how they run. The coach never diagnoses."""
    c = _coach()
    d = _d(date)
    c.store.add_pain(PainReport(date=d, where=where, score=max(0, min(10, score)), changed_stride=changed_stride))
    return today(str(d))


@server.tool()
def keep_original_run(date: str | None = None) -> dict:
    """The runner wants to keep the planned run instead of the easier version on this date."""
    c = _coach()
    d = _d(date)
    from .scheduler import schedule

    for s in schedule(c.plan, c.store.state.changes):
        if s.date == d and not s.dropped:
            c.store.keep_original(d, s.key)
    return today(str(d))


@server.tool()
def propose_paces(distance_km: float, time: str) -> dict:
    """From a test or race result (time as mm:ss or h:mm:ss), propose new paces. Show the text and ask before accept_paces."""
    parts = [int(x) for x in time.split(":")]
    minutes = (parts[0] * 60 + parts[1] + parts[2] / 60) if len(parts) == 3 else (parts[0] + parts[1] / 60)
    return _coach().propose_paces(distance_km, minutes)


@server.tool()
def accept_paces(distance_km: float, time: str) -> dict:
    """Apply the paces from propose_paces, after the runner says yes."""
    c = _coach()
    prop = propose_paces(distance_km, time)
    c.accept_paces(prop)
    return {"applied": prop["text"]}


@server.tool()
def calendar_moved(key: str, date: str | None = None, time: str | None = None, removed: bool = False) -> dict:
    """The runner moved (or deleted) a runcoach event in their calendar app. Keep their choice.

    key: the event's key from calendar_events. date: YYYY-MM-DD. time: HH:MM (optional).
    removed: true if they deleted the event (the run is dropped; km are not made up).
    """
    c = _coach()
    move = Move(date=None) if removed else Move(date=_d(date), time=time)
    words = f"You removed {key} from your calendar." if removed else f"You moved {key} to {move.date:%a %-d %b}" + (f" at {time}." if time else ".")
    c.store.set_move(key, move, words)
    return {"saved": words, "today": today()}


@server.tool()
def back_to_plan(key: str) -> dict:
    """Forget a calendar move for this key, so the coach places the run again."""
    return {"cleared": _coach().store.clear_move(key)}


@server.tool()
def rule_book() -> dict:
    """Every rule the coach uses, with how sure we are and the source."""
    return {"rules": [{"id": r.id, "name": r.name, "why": r.why, "how_sure": r.level_text, "source": r.source} for r in RULES.values()]}


@server.tool()
def calendar_events(start: str | None = None, days: int = 14) -> dict:
    """Events for the runner's Training calendar: title, start, end, description, and a stable id.

    Write them with your calendar connector. Replace an event if the id or key already exists.
    Delete runcoach events in this range that are not in the list (they were dropped).
    """
    c = _coach()
    s = _d(start)
    return {"events": calendar_out.events(c, s, s + dt.timedelta(days=days - 1), today=dt.date.today())}


@server.tool()
def sync_google_calendar(days: int = 28) -> dict:
    """Two-way sync with the Google calendar "Training" (needs Google setup, see README).

    First reads runs the runner moved or deleted in the calendar and keeps that choice,
    then writes the next N days. your_moves lists what it read, in plain words.
    """
    c = _coach()
    s = dt.date.today()
    e = s + dt.timedelta(days=days - 1)
    return calendar_out.GoogleCalendarSync().sync(c, s, e, s)


@server.tool()
def sync_garmin(days: int = 14) -> dict:
    """Read recent activities and body data from Garmin (unofficial login, see README)."""
    from .garmin_in import sync

    return sync(_coach().store, days)


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
