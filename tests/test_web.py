import datetime as dt

from fastapi.testclient import TestClient

from runcoach.engine import Coach
from runcoach.store import Store
from runcoach.web.app import create_app


def client(plan, tmp_path):
    path = tmp_path / "state.json"
    return TestClient(create_app(lambda: Coach(plan, Store(path))))


def test_pages(plan, tmp_path):
    c = client(plan, tmp_path)
    r = c.get("/?d=2027-02-10")
    assert r.status_code == 200 and "Tempo 8 km" in r.text and "9:31 per mile" in r.text and "6.3 mph" in r.text
    assert "Comfortably hard" in r.text
    assert c.get("/plan?d=2027-02-10").status_code == 200
    assert c.get("/change").status_code == 200
    assert c.get("/log").status_code == 200
    assert c.get("/calendar.ics").text.startswith("BEGIN:VCALENDAR")
    assert c.get("/api/today?d=2027-02-10").json()["week"] == 14


def test_change_flow(plan, tmp_path):
    c = client(plan, tmp_path)
    form = {"start": "2027-02-08", "end": "2027-02-11", "kind": "away", "note": "work trip", "d": "2027-02-01"}
    r = c.post("/change", data=form)
    assert "Fri 12 Feb" in r.text and "Apply" in r.text and "big change" in r.text
    r = c.post("/change", data=form | {"apply": "1"}, follow_redirects=True)
    assert "Change applied" in r.text and "work trip" in r.text
    r = c.get("/?d=2027-02-12")
    assert "Tempo 8 km" in r.text and "Moved from Wed 10 Feb" in r.text
    c.post("/undo", data={})
    assert "Rest day" in c.get("/?d=2027-02-12").text


def test_bad_dates(plan, tmp_path):
    c = client(plan, tmp_path)
    r = c.post("/change", data={"start": "2027-02-08", "end": "2027-02-01", "kind": "away"})
    assert r.status_code == 400


def test_feeling(plan, tmp_path):
    c = client(plan, tmp_path)
    c.post("/feel", data={"day": "2027-01-05", "legs": "heavy", "effort": "8"})
    r = c.get("/?d=2027-01-06")
    assert "You said your legs felt heavy yesterday" in r.text


def test_password(plan, tmp_path, monkeypatch):
    monkeypatch.setenv("RUNCOACH_PASSWORD", "s3cret")
    c = client(plan, tmp_path)
    assert c.get("/").status_code == 401
    assert c.get("/", auth=("runcoach", "s3cret")).status_code == 200


def test_404_and_plan_rows_in_day_order(plan, tmp_path):
    c = client(plan, tmp_path)
    r = c.get("/nope")
    assert r.status_code == 404 and "This page does not exist" in r.text
    html = c.get("/plan?d=2026-11-15").text
    week2 = html[html.index("Week 2 ·"):html.index("Week 3 ·")]
    assert week2.index("Mon 16") < week2.index("Wed 18") < week2.index("Thu 19") < week2.index("Sun 22")


def test_friendly_recovery_no_raw_numbers_up_front(plan, tmp_path):
    from runcoach.models import DayMetrics
    path = tmp_path / "state.json"
    store = Store(path)
    day = dt.date(2027, 1, 6)
    store.upsert_metrics([DayMetrics(date=day - dt.timedelta(days=i), sleep_hours=7.5, rhr=50, hrv=55) for i in range(1, 8)]
                         + [DayMetrics(date=day, sleep_hours=6.4, rhr=50, hrv=55, readiness=72)])
    c = TestClient(create_app(lambda: Coach(plan, Store(path))))
    html = c.get("/?d=2027-01-06").text
    assert "You slept about an hour less than usual." in html
    assert "Show the numbers" in html
    front = html.split("Show the numbers")[0]
    assert "6.4" not in front


def test_question_then_answer(plan, tmp_path):
    from runcoach.models import DayMetrics
    path = tmp_path / "state.json"
    Store(path).upsert_metrics([DayMetrics(date=dt.date(2027, 1, 6), readiness=30)])
    c = TestClient(create_app(lambda: Coach(plan, Store(path))))
    html = c.get("/?d=2027-01-06").text
    assert "How do you feel today?" in html and "I feel tired" in html
    c.post("/feel", data={"day": "2027-01-06", "ready": "tired", "d": "2027-01-06"})
    html = c.get("/?d=2027-01-06").text
    assert "You said you feel tired today" in html and "Keep the original run" in html
    key = "w9-wed-workout"
    c.post("/keep", data={"day": "2027-01-06", "key": key})
    html = c.get("/?d=2027-01-06").text
    assert "You chose to keep this run" in html and "Tempo 6 km" in html


def test_pain_form_gives_rest(plan, tmp_path):
    c = client(plan, tmp_path)
    c.post("/pain", data={"day": "2027-01-06", "where": "left knee", "score": "4"})
    html = c.get("/?d=2027-01-06").text
    assert "Rest today" in html and "left knee" in html and "Pain means rest" in html
    assert "Keep the original run" not in html


def test_paces_from_result(plan, tmp_path):
    c = client(plan, tmp_path)
    html = c.get("/plan?km=5&time=26:30").text
    assert "suggests a half marathon in about" in html and "Use these paces" in html
    c.post("/paces", data={"km": "5", "time": "26:30"})
    html = c.get("/?d=2027-02-10").text
    assert "Tempo 8 km" in html and "5:55" not in html.split("Coming up")[0]
    assert c.get("/rules").status_code == 200
