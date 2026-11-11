"""Two-way calendar sync: the runner's own moves and deletes in the calendar app are kept."""

import asyncio
import copy
import datetime as dt
import json

from runcoach import calendar_out
from runcoach.calendar_out import GoogleCalendarSync
from runcoach.models import ChangeKind, LifeChange, Move
from runcoach.scheduler import schedule

D = dt.date
TODAY = D(2027, 2, 1)
START, END = TODAY, TODAY + dt.timedelta(days=27)
TEMPO = "w14-wed-workout"


class _Call:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


class FakeEvents:
    """A tiny stand-in for the Google Calendar events API."""

    def __init__(self):
        self.items: dict[str, dict] = {}

    def list(self, calendarId, timeMin, timeMax, pageToken=None, privateExtendedProperty=None,
             singleEvents=True, showDeleted=False):
        def run():
            out = []
            for e in self.items.values():
                if e.get("status") == "cancelled" and not showDeleted:
                    continue
                st = e["start"].get("dateTime") or e["start"].get("date")
                if timeMin[:10] <= st[:10] < timeMax[:10]:
                    out.append(copy.deepcopy(e))
            return {"items": out}
        return _Call(run)

    def insert(self, calendarId, body):
        def run():
            if body["id"] in self.items:
                raise RuntimeError("409 duplicate")
            self.items[body["id"]] = copy.deepcopy(body)
            return body
        return _Call(run)

    def update(self, calendarId, eventId, body):
        def run():
            self.items[eventId] = copy.deepcopy(body)
            return body
        return _Call(run)

    def delete(self, calendarId, eventId):
        def run():
            self.items[eventId]["status"] = "cancelled"
        return _Call(run)


class FakeService:
    def __init__(self):
        self.ev = FakeEvents()

    def events(self):
        return self.ev

    def calendarList(self):
        class L:
            def list(self, pageToken=None):
                return _Call(lambda: {"items": [{"id": "cal1", "summary": "Training"}]})
        return L()


def _sync():
    s = GoogleCalendarSync()
    s._svc = FakeService()
    return s


def _by_key(sync):
    return {e["extendedProperties"]["private"]["key"]: e for e in sync._svc.ev.items.values()}


def _user_moves(sync, key, new_start):
    e = _by_key(sync)[key]
    e["start"] = {"dateTime": new_start, "timeZone": "UTC"}


def test_scheduler_keeps_a_calendar_move(plan):
    items = {s.key: s for s in schedule(plan, moves={TEMPO: Move(date=D(2027, 2, 11))})}
    tempo = items[TEMPO]
    assert tempo.date == D(2027, 2, 11)
    assert tempo.adjustments[-1].rule == "you_chose"
    assert "You moved this run in your calendar from Wed 10 Feb" in tempo.adjustments[-1].reason
    # The other runs in the week still follow the rules around it.
    week = [s for s in items.values() if s.week == 14 and not s.dropped]
    assert len({s.date for s in week}) == len(week)


def test_scheduler_drops_a_removed_run(plan):
    items = {s.key: s for s in schedule(plan, moves={TEMPO: Move(date=None)})}
    assert items[TEMPO].dropped
    assert "removed this run from your calendar" in items[TEMPO].adjustments[-1].reason


def test_life_change_wins_over_an_old_move(plan):
    away = LifeChange(start=D(2027, 2, 11), end=D(2027, 2, 11), kind=ChangeKind.away)
    items = {s.key: s for s in schedule(plan, [away], moves={TEMPO: Move(date=D(2027, 2, 11))})}
    assert items[TEMPO].date != D(2027, 2, 11)


def test_two_way_sync_keeps_the_runners_move(coach):
    sync = _sync()
    first = sync.sync(coach, START, END, TODAY)
    assert first["created"] > 0 and first["your_moves"] == []

    # The runner drags the tempo run from Wed 10 Feb 07:00 to Thu 11 Feb 18:30.
    _user_moves(sync, TEMPO, "2027-02-11T18:30:00+00:00")
    r = sync.sync(coach, START, END, TODAY)
    assert r["your_moves"] and "Thu 11 Feb at 18:30" in r["your_moves"][0]
    assert coach.store.state.moves[TEMPO] == Move(date=D(2027, 2, 11), time="18:30")
    ev = _by_key(sync)[TEMPO]
    assert ev["start"]["dateTime"].startswith("2027-02-11T18:30")   # not moved back
    assert "You moved this run in your calendar" in ev["description"]

    # A third sync finds nothing new and changes nothing.
    again = sync.sync(coach, START, END, TODAY)
    assert again["your_moves"] == [] and again["updated"] == 0 and again["created"] == 0


def test_two_way_sync_keeps_a_delete(coach):
    sync = _sync()
    sync.sync(coach, START, END, TODAY)
    eid = _by_key(sync)[TEMPO]["id"]
    sync._svc.ev.items[eid]["status"] = "cancelled"     # the runner deletes it
    r = sync.sync(coach, START, END, TODAY)
    assert any("removed" in x for x in r["your_moves"])
    assert sync._svc.ev.items[eid]["status"] == "cancelled"   # not put back
    assert coach.store.state.moves[TEMPO].removed


def test_runcoach_own_delete_is_not_a_runner_delete(coach):
    sync = _sync()
    sync.sync(coach, START, END, TODAY)
    coach.apply(LifeChange(start=D(2027, 2, 8), end=D(2027, 2, 14), kind=ChangeKind.away, note="trip"), TODAY)
    r = sync.sync(coach, START, END, TODAY)
    assert r["deleted"] > 0 and r["your_moves"] == []
    coach.undo()
    r = sync.sync(coach, START, END, TODAY)
    assert r["your_moves"] == [] and coach.store.state.moves == {}
    assert _by_key(sync)[TEMPO].get("status") == "confirmed"


def test_undo_puts_a_moved_run_back(coach):
    coach.store.set_move(TEMPO, Move(date=D(2027, 2, 11)), "You moved it.")
    assert next(s for s in coach.sessions(TODAY) if s.key == TEMPO).date == D(2027, 2, 11)
    coach.undo()
    assert next(s for s in coach.sessions(TODAY) if s.key == TEMPO).date == D(2027, 2, 10)


def test_moved_time_is_used_in_events(coach):
    coach.store.set_move(TEMPO, Move(date=D(2027, 2, 11), time="18:30"), "moved")
    ev = next(e for e in calendar_out.events(coach, START, END, TODAY) if e["key"] == TEMPO)
    assert ev["start"].startswith("2027-02-11T18:30")


def test_mcp_calendar_moved(monkeypatch, tmp_path):
    from pathlib import Path

    monkeypatch.setenv("RUNCOACH_PLAN", str(Path(__file__).resolve().parents[1] / "plans" / "sample-half-marathon.yaml"))
    monkeypatch.setenv("RUNCOACH_STATE", str(tmp_path / "s.json"))
    from runcoach.mcp_server import server

    async def go():
        r = await server.call_tool("calendar_moved", {"key": TEMPO, "date": "2027-02-11", "time": "18:30"})
        assert "Thu 11 Feb at 18:30" in json.loads(r.content[0].text)["saved"]
        r = await server.call_tool("week", {"n": 14, "date": "2027-02-01"})
        lines = [s["line"] for s in json.loads(r.content[0].text)["sessions"]]
        assert any(x.startswith("Thu 11 Feb: Tempo") for x in lines)
        r = await server.call_tool("back_to_plan", {"key": TEMPO})
        assert json.loads(r.content[0].text)["cleared"] is True

    asyncio.run(go())
