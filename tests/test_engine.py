import datetime as dt

from runcoach.models import ChangeKind, DayMetrics, LifeChange

D = dt.date


def test_preview_and_apply_and_undo(coach):
    today = D(2027, 2, 1)
    trip = LifeChange(start=D(2027, 2, 8), end=D(2027, 2, 11), kind=ChangeKind.away, note="work trip")
    diff = coach.preview(trip, today)
    assert len(diff.moved) == 1 and diff.moved[0][1].date == D(2027, 2, 12)
    assert len(diff.dropped) == 1 and diff.dropped[0].session.type == "easy"
    assert {e.date for e in diff.lifts_skipped} == {D(2027, 2, 8), D(2027, 2, 11)}
    assert diff.needs_ok  # the workout moves 2 days
    assert coach.store.state.changes == []  # preview does not save

    coach.apply(trip, today)
    assert len(coach.store.state.changes) == 1
    assert coach.on(D(2027, 2, 12), today)[0].session.type == "workout"

    coach.undo()
    assert coach.store.state.changes == []
    assert coach.on(D(2027, 2, 10), today)[0].session.type == "workout"


def test_dropping_long_run_needs_ok(coach):
    c = LifeChange(start=D(2027, 2, 13), end=D(2027, 2, 14), kind=ChangeKind.away, note="wedding")
    diff = coach.preview(c, D(2027, 2, 1))
    assert diff.needs_ok
    assert any(a.session.type == "long" for _, a in diff.moved)


def test_one_day_move_is_small(coach):
    c = LifeChange(start=D(2027, 1, 6), end=D(2027, 1, 6), kind=ChangeKind.busy, note="late meeting")
    diff = coach.preview(c, D(2027, 1, 4))
    assert [(b.date, a.date) for b, a in diff.moved] == [(D(2027, 1, 6), D(2027, 1, 7))]
    assert not diff.needs_ok


def test_today_view(coach):
    day = D(2027, 1, 6)
    coach.store.upsert_metrics([DayMetrics(date=day, readiness=82, rhr=50, hrv=58, sleep_hours=7.5)])
    t = coach.today(day)
    assert t["week"] == 9
    assert t["runs"][0].session.type == "workout"
    assert t["metrics"].readiness == 82
