"""Friendly, plain words for numbers.

The runner should never have to read "5.1 h" or "HRV 42". We compare each
number with the runner's own normal and say what it means. Raw numbers are
still available behind "Show the numbers".
"""

from __future__ import annotations

import datetime as dt
from statistics import median

from .models import DayMetrics, Plan

FEEL = {
    "E": "Easy: you can chat in full sentences",
    "HMP": "Race pace: steady, a few words at a time",
    "T": "Comfortably hard: short sentences only",
    "I": "Hard: one or two words at a time",
}


def feel(pace_key: str) -> str:
    return FEEL.get(pace_key, FEEL["E"])


def hours(h: float) -> str:
    half = round(h * 2) / 2
    whole = int(half)
    if half == 0.5:
        return "about half an hour"
    if half - whole:
        return f"about {whole} and a half hours"
    return "about 1 hour" if whole == 1 else f"about {whole} hours"


def hours_diff(delta: float) -> str:
    d = round(abs(delta) * 2) / 2
    if d < 0.5:
        return "about as much as usual"
    word = "less" if delta < 0 else "more"
    amount = {0.5: "half an hour", 1.0: "an hour", 1.5: "an hour and a half"}.get(d, f"{d:g} hours")
    return f"about {amount} {word} than usual"


def minutes(m: float) -> str:
    m = int(round(m / 5) * 5) or 5
    if m < 60:
        return f"About {m} minutes"
    h, r = divmod(m, 60)
    return f"About {h} hour{'s' if h > 1 else ''}" + (f" {r} minutes" if r else "")


def usual_sleep(history: list[DayMetrics], day: dt.date) -> float | None:
    vals = [m.sleep_hours for m in history
            if m.sleep_hours is not None and day - dt.timedelta(days=14) <= m.date < day]
    return median(vals) if len(vals) >= 3 else None


def usual_rhr(plan: Plan, history: list[DayMetrics], day: dt.date) -> float | None:
    if plan.athlete.rhr_normal:
        return float(plan.athlete.rhr_normal[1])
    vals = [m.rhr for m in history if m.rhr is not None and day - dt.timedelta(days=28) <= m.date < day]
    return median(vals) if len(vals) >= 5 else None


def sleep_short(m: DayMetrics | None, history: list[DayMetrics]) -> str | None:
    """A plain sentence if last night was clearly short, else None."""
    if not m or m.sleep_hours is None:
        return None
    usual = usual_sleep(history, m.date)
    if usual is not None and m.sleep_hours - usual <= -1:
        return f"You slept {hours_diff(m.sleep_hours - usual)}."
    if m.sleep_hours < 5.5:
        return "You had a short night of sleep."
    return None


def recovery(plan: Plan, today: dt.date, history: list[DayMetrics]) -> dict:
    """A simple recovery summary: state (good/okay/low/unknown), a headline, plain lines, and the raw numbers."""
    m = next((x for x in history if x.date == today), None)
    if not m:
        return {"state": "unknown", "headline": "No recovery data yet today",
                "lines": ["Sync your watch, or tell us how you feel below."], "numbers": []}
    lines, flags = [], 0
    # Sleep
    if m.sleep_hours is not None:
        usual = usual_sleep(history, today)
        short = sleep_short(m, history)
        if short:
            lines.append(short)
            flags += 1
        elif usual is not None:
            lines.append(f"You slept {hours_diff(m.sleep_hours - usual)}.")
        else:
            lines.append(f"You slept {hours(m.sleep_hours)}.")
    # Resting heart rate
    normal = usual_rhr(plan, history, today)
    if m.rhr is not None and normal is not None:
        if m.rhr > normal + 3:
            lines.append("Your heart is working harder than usual at rest. This can mean stress, poor sleep, or a cold coming.")
            flags += 1
        elif m.rhr > normal:
            lines.append("Your heart rate at rest is a little higher than usual.")
        else:
            lines.append("Your heart rate at rest is normal.")
    # HRV
    base = plan.athlete.hrv_baseline
    if m.hrv is not None and base:
        if m.hrv < base * 0.8:
            lines.append("Your body shows signs of stress.")
            flags += 1
        else:
            lines.append("Your body shows no signs of extra stress.")
    # Watch readiness
    if m.readiness is not None:
        if m.readiness < 40:
            lines.append("Your watch says you have not fully recovered.")
            flags += 1
        elif m.readiness >= 70:
            lines.append("Your watch says you are ready to train.")
    state, headline = (
        ("good", "Your body looks ready") if flags == 0 else
        ("okay", "Mostly ready, one sign is a bit off") if flags == 1 else
        ("low", "Your body needs an easier day")
    )
    numbers = []
    if m.sleep_hours is not None:
        numbers.append(("Sleep", f"{m.sleep_hours:g} h"))
    if m.rhr is not None:
        numbers.append(("Resting heart rate", f"{m.rhr:.0f} bpm"))
    if m.hrv is not None:
        numbers.append(("Heart rate variability", f"{m.hrv:.0f} ms" + (f" (your normal: {base:.0f})" if base else "")))
    if m.readiness is not None:
        numbers.append(("Watch readiness", f"{m.readiness:.0f} of 100"))
    return {"state": state, "headline": headline, "lines": lines, "numbers": numbers}


def day_name(d: dt.date, today: dt.date | None = None) -> str:
    today = today or dt.date.today()
    delta = (d - today).days
    if delta == 0:
        return "today"
    if delta == -1:
        return "yesterday"
    if delta == 1:
        return "tomorrow"
    if -6 <= delta < 0:
        return f"on {d:%A}"
    return f"on {d:%a %-d %b}"
