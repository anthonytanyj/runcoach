"""Change sessions from how your body is doing. Every change names a rule in ``rules.py``.

How the coach decides (in this order):
  1. You chose to keep the original run          -> keep it (rule you_chose)
  2. Pain that changes your stride, or 4/10+     -> rest today (pain_stop)
     Mild pain (1-3/10)                          -> easier run (pain_mild)
     Pain 3 days in a row                        -> suggest a physio (pain_see_someone)
  3. The first 2 runs after an illness           -> easier run (illness_return)
  4. You said you feel tired, or legs heavy,
     or yesterday felt very hard (effort 8+)     -> easier run (you_said_tired)
  5. Two or more signs agree (short sleep, high
     resting heart rate 2 days, low HRV, low
     watch readiness, a big hike)                -> easier run (signs_agree)
  6. Only one sign                               -> ask you first (one_sign_ask)
The race is never changed automatically; the coach only adds a note.
Easier run = easy pace only, about 25% shorter.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from .models import Activity, Adjustment, ChangeKind, DayMetrics, LifeChange, PainReport, Plan, Scheduled
from .words import sleep_short, usual_rhr

ONE_DAY = dt.timedelta(days=1)


@dataclass
class Rules:
    readiness_min: float = 40
    rhr_over_normal: float = 3
    rhr_days: int = 2
    hrv_ratio_min: float = 0.8
    hike_ascent_m: float = 300
    hike_days: int = 2
    after_illness_runs: int = 2
    easy_factor: float = 0.75
    pain_days_for_check: int = 3


def _round_half(x: float) -> float:
    return max(2.0, round(x * 2) / 2)


def easy_version(item: Scheduled, reason: str, rule: str, factor: float) -> Scheduled:
    s = item.session
    km = _round_half(s.km * factor)
    new = s.model_copy(update={
        "type": "easy" if s.type in ("workout", "test") else s.type,
        "pace": "E", "km": km, "strides": 0,
        "title": f"Easy version of: {s.title or s.detail or s.type}",
        "detail": f"Easy pace only. The plan said {s.km:g} km{' (' + s.detail + ')' if s.detail else ''}.",
    })
    out = item.model_copy(update={"session": new})
    out.adjustments = [*item.adjustments, Adjustment(kind="easier", reason=reason, rule=rule)]
    return out


def _note(item: Scheduled, kind: str, reason: str, rule: str) -> Scheduled:
    out = item.model_copy()
    out.adjustments = [*item.adjustments, Adjustment(kind=kind, reason=reason, rule=rule)]
    return out


def body_signs(d: dt.date, plan: Plan, metrics: list[DayMetrics], activities: list[Activity], r: Rules) -> list[str]:
    """Signs from the watch and other activities, each as a plain sentence."""
    by_day = {m.date: m for m in metrics}
    m = by_day.get(d)
    out: list[str] = []
    short = sleep_short(m, metrics)
    if short:
        out.append(short)
    normal = usual_rhr(plan, metrics, d)
    if normal is not None:
        days = [by_day.get(d - i * ONE_DAY) for i in range(r.rhr_days)]
        if all(x and x.rhr is not None and x.rhr > normal + r.rhr_over_normal for x in days):
            out.append(f"Your heart rate at rest has been higher than usual for {r.rhr_days} days.")
    base = plan.athlete.hrv_baseline
    if m and m.hrv is not None and base and m.hrv < base * r.hrv_ratio_min:
        out.append("Your body shows signs of stress.")
    if m and m.readiness is not None and m.readiness < r.readiness_min:
        out.append("Your watch says you have not fully recovered.")
    for a in activities:
        if "hik" in a.type.lower() and a.ascent_m and a.ascent_m >= r.hike_ascent_m:
            if 1 <= (d - a.date).days <= r.hike_days:
                out.append(f"You did a hike with lots of climbing on {a.date:%A}.")
    return out


def you_said(d: dt.date, feelings: dict[dt.date, dict]) -> str | None:
    today = feelings.get(d, {})
    if today.get("ready") == "tired":
        return "You said you feel tired today."
    y = feelings.get(d - ONE_DAY, {})
    if y.get("legs") == "heavy":
        return "You said your legs felt heavy yesterday."
    if (y.get("effort") or 0) >= 8:
        return "You said yesterday felt very hard."
    return None


def pain_for(d: dt.date, pains: list[PainReport]) -> PainReport | None:
    """The newest pain report from today or yesterday, if it reports any pain."""
    recent = [p for p in pains if d - ONE_DAY <= p.date <= d]
    if not recent:
        return None
    last = max(recent, key=lambda p: p.date)
    return last if (last.score > 0 or last.changed_stride) else None


def pain_streak(d: dt.date, pains: list[PainReport]) -> int:
    days = {p.date for p in pains if p.score > 0 or p.changed_stride}
    n, x = 0, d
    while x in days:
        n += 1
        x -= ONE_DAY
    return n


def adapt(
    plan: Plan,
    items: list[Scheduled],
    metrics: list[DayMetrics] | None = None,
    activities: list[Activity] | None = None,
    changes: list[LifeChange] | None = None,
    feelings: dict[dt.date, dict] | None = None,
    rules: Rules | None = None,
    pains: list[PainReport] | None = None,
    overrides: list[str] | None = None,
) -> list[Scheduled]:
    r = rules or Rules()
    metrics = metrics or []
    acts = activities or []
    changes = changes or []
    feelings = feelings or {}
    pains = pains or []
    keep = set(overrides or [])

    ill_ends = sorted(c.end for c in changes if c.kind == ChangeKind.ill)
    runs_after: dict[dt.date, int] = {}
    for end in ill_ends:
        n = 0
        for it in items:
            if it.date > end and not it.dropped:
                runs_after[it.date] = min(runs_after.get(it.date, n), n)
                n += 1
                if n >= r.after_illness_runs:
                    break

    out: list[Scheduled] = []
    for it in items:
        if it.dropped:
            out.append(it)
            continue
        d = it.date
        race = it.session.type == "race"
        pain = pain_for(d, pains)

        if f"{d.isoformat()}|{it.key}" in keep:
            out.append(_note(it, "kept", "You chose to keep this run as planned.", "you_chose"))
            continue

        if pain and pain.stop:
            where = f" in your {pain.where}" if pain.where else ""
            msg = (f"You reported pain{where} that {'changes how you run' if pain.changed_stride else 'feels strong'}. "
                   "Rest today. Running through it can make it worse.")
            if race:
                out.append(_note(it, "note", msg + " Think hard before you race, and ask a physio if you can.", "pain_stop"))
                continue
            rest = _note(it, "no_run", msg, "pain_stop").model_copy(update={"status": "rest"})
            if pain_streak(d, pains) >= r.pain_days_for_check:
                rest.adjustments.append(Adjustment(kind="note", rule="pain_see_someone",
                                                   reason="This pain has lasted several days. Please have a physio or doctor look at it."))
            out.append(rest)
            continue
        if race:
            out.append(it)
            continue
        if pain:
            where = f" in your {pain.where}" if pain.where else ""
            item = easy_version(it, f"You reported mild pain{where}. Keep it easy, and stop if it gets worse.", "pain_mild", r.easy_factor)
            if pain_streak(d, pains) >= r.pain_days_for_check:
                item.adjustments.append(Adjustment(kind="note", rule="pain_see_someone",
                                                   reason="This pain has lasted several days. Please have a physio or doctor look at it."))
            out.append(item)
            continue

        n = runs_after.get(d)
        if n is not None and n < r.after_illness_runs:
            out.append(easy_version(it, "You were sick recently, so this run is easy to help your body catch up.", "illness_return", r.easy_factor))
            continue

        said = you_said(d, feelings)
        if said:
            out.append(easy_version(it, f"{said} So today's run is easier.", "you_said_tired", r.easy_factor))
            continue

        signs = body_signs(d, plan, metrics, acts, r)
        if len(signs) >= 2:
            out.append(easy_version(it, "Two signs say you need an easier day. " + " ".join(signs[:3]), "signs_agree", r.easy_factor))
        elif len(signs) == 1:
            if feelings.get(d, {}).get("ready") == "fine":
                out.append(_note(it, "kept", f"{signs[0]} You said you feel fine, so the run stays as planned.", "one_sign_ask"))
            else:
                out.append(_note(it, "ask", f"{signs[0]} How do you feel today?", "one_sign_ask"))
        else:
            out.append(it)
    return out


def mark_status(items: list[Scheduled], activities: list[Activity], today: dt.date) -> list[Scheduled]:
    """Mark past sessions as done (a run that day) or missed."""
    run_days = {a.date for a in activities if a.is_run}
    out = []
    for it in items:
        if it.dropped or it.status == "rest":
            out.append(it)
        elif it.date in run_days:
            out.append(it.model_copy(update={"status": "done"}))
        elif it.date < today:
            out.append(it.model_copy(update={"status": "missed"}))
        else:
            out.append(it)
    return out
