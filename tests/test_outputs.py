import asyncio
import datetime as dt
import json

from runcoach import calendar_out
from runcoach.models import ChangeKind, LifeChange
from runcoach.render import title

D = dt.date


def test_event_titles(coach):
    items = {s.date: s for s in coach.sessions(D(2027, 1, 1))}
    assert title(coach.plan, items[D(2027, 2, 10)]) == "Tempo 8 km · 2 x 12 min tempo @ 5:55/km"
    assert title(coach.plan, items[D(2027, 2, 13)]) == "Easy 5 km · 7:00–7:30/km"
    assert title(coach.plan, items[D(2026, 11, 18)]) == "Easy 5 km + 4 strides · 7:00–7:30/km"
    assert title(coach.plan, items[D(2027, 4, 25)]).startswith("Spring Half Marathon")


def test_events_and_ids_are_stable_when_moved(coach):
    today = D(2027, 2, 1)
    before = {e["key"]: e for e in calendar_out.events(coach, D(2027, 2, 8), D(2027, 2, 14), today)}
    coach.apply(LifeChange(start=D(2027, 2, 8), end=D(2027, 2, 11), kind=ChangeKind.away, note="trip"), today)
    after = {e["key"]: e for e in calendar_out.events(coach, D(2027, 2, 8), D(2027, 2, 14), today)}
    wk = "w14-wed-workout"
    assert before[wk]["id"] == after[wk]["id"]
    assert after[wk]["start"].startswith("2027-02-12T07:00")
    assert "Why this changed" in after[wk]["description"]
    assert "w14-sat-easy" not in after  # dropped
    assert not any(k.startswith("block-") and "2027-02-08" in k for k in after)  # lift skipped on trip
    long = after["w14-sun-long"]
    assert long["start"].startswith("2027-02-14T08:00")
    assert "mph" in long["description"] and "per mile" in long["description"]
    assert "you can chat" in long["description"]


def test_ics(coach):
    evts = calendar_out.events(coach, D(2026, 11, 16), D(2026, 11, 22), D(2026, 11, 16))
    ics = calendar_out.to_ics(evts)
    assert ics.startswith("BEGIN:VCALENDAR") and ics.count("BEGIN:VEVENT") == len(evts) == 4
    assert all(len(line.encode()) <= 75 for line in ics.split("\r\n"))


def test_mcp_tools(monkeypatch, plan, tmp_path):
    from pathlib import Path

    monkeypatch.setenv("RUNCOACH_PLAN", str(Path(__file__).resolve().parents[1] / "plans" / "sample-half-marathon.yaml"))
    monkeypatch.setenv("RUNCOACH_STATE", str(tmp_path / "s.json"))
    from runcoach.mcp_server import server

    async def go():
        names = {t.name for t in await server.list_tools()}
        assert {"today", "preview_change", "apply_change", "calendar_events", "log_metrics"} <= names
        r = await server.call_tool("preview_change", {"start": "2027-02-08", "end": "2027-02-11", "note": "trip"})
        data = json.loads(r.content[0].text)
        assert data["needs_ok"] is True
        assert any("Fri 12 Feb" in x for x in data["changes"])
        await server.call_tool("log_metrics", {"date": "2027-02-14", "readiness": 30, "hrv": 35})
        r = await server.call_tool("today", {"date": "2027-02-14"})
        today = json.loads(r.content[0].text)
        assert today["runs"][0]["line"] == "Sun 14 Feb: Long 12 km (made easier) · 7:00–7:30/km"
        assert "Two signs" in today["runs"][0]["details"]

    asyncio.run(go())
