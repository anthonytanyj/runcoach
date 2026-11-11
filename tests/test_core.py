import datetime as dt

from runcoach.adapt import adapt
from runcoach.models import Activity, ChangeKind, DayMetrics, LifeChange, PainReport
from runcoach.paces import describe
from runcoach.scheduler import schedule

D = dt.date


def by_week(items, n):
    return {i.session.type + ("" if i.session.type != "easy" else ""): i for i in items if i.week == n}


def test_paces_all_units():
    p = describe(["7:00", "7:30"])
    assert p["km"] == "7:00–7:30/km"
    assert p["mi"] == "11:16–12:04/mi"
    assert p["kmh"] == "8.0–8.6 km/h"
    assert p["mph"] == "5.0–5.3 mph"
    assert describe(["6:24"])["mi"] == "10:18/mi"


def test_plan_shape(plan):
    assert len(plan.weeks) == 24
    assert plan.week_start(24) + dt.timedelta(days=6) == plan.race.date == D(2027, 4, 25)


def test_no_changes_keeps_slots(plan):
    items = schedule(plan)
    assert all(i.date == i.original_date for i in items)
    assert not any(i.dropped for i in items)
    race = [i for i in items if i.session.type == "race"]
    assert [r.date for r in race] == [D(2027, 2, 28), D(2027, 4, 25)]


def test_week14_trip_moves_workout_and_drops_easy(plan):
    """Trip Mon 8 - Thu 11 Feb: Wed tempo -> Fri 12 Feb, Sat easy dropped, Sun long stays."""
    trip = LifeChange(start=D(2027, 2, 8), end=D(2027, 2, 11), kind=ChangeKind.away, note="work trip")
    items = [i for i in schedule(plan, [trip]) if i.week == 14]
    workout = next(i for i in items if i.session.type == "workout")
    easy = next(i for i in items if i.session.type == "easy")
    long = next(i for i in items if i.session.type == "long")
    assert workout.date == D(2027, 2, 12)
    assert workout.adjustments[0].kind == "moved"
    assert easy.dropped
    assert long.date == D(2027, 2, 14) and not long.adjustments


def test_hard_runs_never_next_to_each_other(plan):
    changes = [LifeChange(start=D(2027, 3, 15), end=D(2027, 3, 18), kind=ChangeKind.busy)]
    items = [i for i in schedule(plan, changes) if not i.dropped]
    hard = sorted(i.date for i in items if i.session.hard)
    assert all((b - a).days >= 2 for a, b in zip(hard, hard[1:]))


def test_never_three_run_days_in_a_row(plan):
    changes = [LifeChange(start=D(2027, 1, 4), end=D(2027, 1, 7), kind=ChangeKind.away)]
    days = sorted(i.date for i in schedule(plan, changes) if not i.dropped)
    s = set(days)
    assert not any(d + dt.timedelta(days=1) in s and d + dt.timedelta(days=2) in s for d in days)


def test_race_never_moves(plan):
    c = LifeChange(start=D(2027, 4, 19), end=D(2027, 4, 25), kind=ChangeKind.busy)
    race = [i for i in schedule(plan, [c]) if i.session.type == "race" and i.week == 24][0]
    assert race.date == D(2027, 4, 25)
    assert race.adjustments  # flagged for the runner to check


def test_not_before_today(plan):
    # Today is Thu 7 Jan (week 9). Wed workout was yesterday; a change today must not move runs into the past.
    c = LifeChange(start=D(2027, 1, 9), end=D(2027, 1, 9), kind=ChangeKind.busy)
    items = [i for i in schedule(plan, [c], not_before=D(2027, 1, 7)) if i.week == 9]
    assert all(i.date >= D(2027, 1, 7) or i.date == i.original_date for i in items)


def _on(out, day):
    return next(i for i in out if i.date == day)


def test_one_sign_only_asks(plan):
    """One low number alone never changes the run. The coach asks first."""
    items = schedule(plan)
    day = D(2027, 1, 6)  # week 9 Wed tempo
    s = _on(adapt(plan, items, metrics=[DayMetrics(date=day, readiness=31)]), day)
    assert s.session.type == "workout"
    assert s.adjustments[-1].kind == "ask" and s.adjustments[-1].rule == "one_sign_ask"
    assert "How do you feel" in s.adjustments[-1].reason


def test_one_sign_and_you_feel_fine_keeps_run(plan):
    items = schedule(plan)
    day = D(2027, 1, 6)
    s = _on(adapt(plan, items, metrics=[DayMetrics(date=day, readiness=31)], feelings={day: {"ready": "fine"}}), day)
    assert s.session.type == "workout" and s.adjustments[-1].kind == "kept"


def test_two_signs_agree_gives_easy_version(plan):
    items = schedule(plan)
    day = D(2027, 1, 6)
    m = [DayMetrics(date=day, readiness=31, hrv=38)]
    s = _on(adapt(plan, items, metrics=m), day)
    assert s.session.type == "easy" and s.session.pace == "E" and s.session.km == 4.5
    assert s.adjustments[-1].rule == "signs_agree"
    assert "Two signs" in s.adjustments[-1].reason


def test_you_said_tired_is_enough(plan):
    items = schedule(plan)
    day = D(2027, 1, 6)
    s = _on(adapt(plan, items, feelings={day: {"ready": "tired"}}), day)
    assert s.session.type == "easy" and s.adjustments[-1].rule == "you_said_tired"


def test_short_sleep_compared_with_your_normal(plan):
    items = schedule(plan)
    day = D(2027, 1, 10)  # Sun long 12 km
    hist = [DayMetrics(date=day - dt.timedelta(days=i), sleep_hours=7.5) for i in range(1, 8)]
    hist.append(DayMetrics(date=day, sleep_hours=6.1))
    s = _on(adapt(plan, items, metrics=hist), day)
    assert s.adjustments[-1].kind == "ask"
    assert "an hour and a half less than usual" in s.adjustments[-1].reason
    assert "6.1" not in s.adjustments[-1].reason


def test_rhr_two_days_high_plus_hike(plan):
    items = schedule(plan)
    day = D(2027, 1, 10)  # Sun long 12 km
    m = [DayMetrics(date=day - dt.timedelta(days=1), rhr=61), DayMetrics(date=day, rhr=60)]
    hike = Activity(date=D(2027, 1, 9), type="hiking", km=9.6, ascent_m=415)
    s = _on(adapt(plan, items, metrics=m, activities=[hike]), day)
    assert s.session.km == 9 and s.session.type == "long"
    assert "heart rate at rest" in s.adjustments[-1].reason and "hike" in s.adjustments[-1].reason


def test_pain_that_changes_stride_means_rest(plan):
    items = schedule(plan)
    day = D(2027, 1, 6)
    out = adapt(plan, items, pains=[PainReport(date=day, where="left knee", score=3, changed_stride=True)])
    s = _on(out, day)
    assert s.status == "rest" and s.adjustments[-1].rule == "pain_stop"
    assert "left knee" in s.adjustments[-1].reason


def test_mild_pain_easier_and_lasting_pain_needs_check(plan):
    items = schedule(plan)
    day = D(2027, 1, 10)
    pains = [PainReport(date=day - dt.timedelta(days=i), where="calf", score=2) for i in range(3)]
    s = _on(adapt(plan, items, pains=pains), day)
    assert s.session.pace == "E" and s.status != "rest"
    rules = [a.rule for a in s.adjustments]
    assert "pain_mild" in rules and "pain_see_someone" in rules


def test_pain_zero_today_clears_yesterday(plan):
    items = schedule(plan)
    day = D(2027, 1, 6)
    pains = [PainReport(date=day - dt.timedelta(days=1), score=5), PainReport(date=day, score=0)]
    s = _on(adapt(plan, items, pains=pains), day)
    assert s.status != "rest" and not s.adjustments


def test_keep_original_override(plan):
    items = schedule(plan)
    day = D(2027, 1, 6)
    it = _on(items, day)
    s = _on(adapt(plan, items, metrics=[DayMetrics(date=day, readiness=20, hrv=30)],
                  overrides=[f"{day.isoformat()}|{it.key}"]), day)
    assert s.session.type == "workout" and s.adjustments[-1].rule == "you_chose"


def test_race_not_auto_changed(plan):
    items = schedule(plan)
    day = D(2027, 4, 25)
    s = _on(adapt(plan, items, metrics=[DayMetrics(date=day, readiness=10, hrv=20)]), day)
    assert s.session.type == "race" and s.session.km == 21.1


def test_illness_easy_return(plan):
    ill = LifeChange(start=D(2027, 1, 4), end=D(2027, 1, 8), kind=ChangeKind.ill)
    items = schedule(plan, [ill])
    out = adapt(plan, items, changes=[ill])
    after = [i for i in out if i.date > D(2027, 1, 8) and not i.dropped][:3]
    assert after[0].session.pace == "E" and after[0].adjustments[-1].rule == "illness_return"
    assert after[1].adjustments[-1].rule == "illness_return"
    assert not any(a.rule == "illness_return" for a in after[2].adjustments)


def test_moved_run_avoids_lift_days(plan):
    # Wed 18 Nov busy: the easy run must not go to Thu 19 (lift day) or Tue/Fri (day after a lift).
    c = LifeChange(start=D(2026, 11, 18), end=D(2026, 11, 18), kind=ChangeKind.busy)
    easy = next(i for i in schedule(plan, [c]) if i.week == 2 and i.session.type == "easy")
    assert easy.date == D(2026, 11, 21)


def test_planned_run_on_lift_day_stays(plan):
    # Week 1: the plan itself puts the run on Thu (a lift day). It must not move.
    thu = next(i for i in schedule(plan) if i.week == 1 and i.session.type == "easy")
    assert thu.date == D(2026, 11, 12)
