"""Text for one session: calendar title, description, and short lines for chat."""

from __future__ import annotations

import datetime as dt

from .models import Plan, Scheduled
from .paces import describe, km_to_mi, parse_pace

TYPE_NAME = {"easy": "Easy", "long": "Long", "workout": "Workout", "test": "Test", "race": "Race"}


def pace_info(plan: Plan, key: str) -> dict[str, str]:
    p = plan.paces.get(key) or plan.paces["E"]
    return describe(p.min_per_km) | {"name": p.name, "hr": p.hr or ""}


def title(plan: Plan, it: Scheduled) -> str:
    s = it.session
    if it.status == "rest":
        return "Rest today · listen to your body"
    p = pace_info(plan, s.pace)
    if s.type == "race":
        return f"{s.title or 'Race'} · {s.km:g} km"
    head = s.title if s.title and s.type != "easy" else f"{TYPE_NAME[s.type]} {s.km:g} km"
    if s.title and s.title.startswith("Easy version"):
        head = f"{TYPE_NAME[s.type]} {s.km:g} km (made easier)"
    if s.strides:
        head += f" + {s.strides} strides"
    if s.type == "test":
        return f"{s.title or 'Test'} {s.km:g} km · {s.detail or ''}".rstrip(" ·")
    if s.type == "workout":
        main = (s.detail or "").split(" / ")[0]
        return f"{p['name']} {s.km:g} km · {main} @ {p['km']}".replace(" ·  @", " ·")
    return f"{head} · {p['km']}"


def short_title(plan: Plan, it: Scheduled) -> str:
    """The title without the pace (for screens that show the pace on its own)."""
    full = title(plan, it)
    return full.rsplit(" @ ", 1)[0] if " @ " in full else full.rsplit(" · ", 1)[0] if it.session.type in ("easy", "long") else full


def est_minutes(plan: Plan, it: Scheduled) -> int:
    s = it.session
    e = plan.paces.get("E") or next(iter(plan.paces.values()))
    slow = max(parse_pace(x) for x in e.min_per_km)
    mins = s.km * slow
    if s.type in ("workout", "test"):
        mins += 10  # warm-up walk and cool-down
    mins += s.strides * 1
    return int(round(mins / 5) * 5) or 30


def start_time(plan: Plan, it: Scheduled) -> dt.time:
    t = plan.athlete.long_run_time if it.session.type in ("long", "race") else plan.athlete.run_time
    h, m = t.split(":")
    return dt.time(int(h), int(m))


def description(plan: Plan, it: Scheduled) -> str:
    """Calendar event text: friendly words first, numbers after."""
    from .rules import RULES
    from .words import feel, minutes

    s = it.session
    p = pace_info(plan, s.pace)
    e = pace_info(plan, "E")
    if it.status == "rest":
        lines = ["Rest today. No run."]
    else:
        lines = [
            f"{s.km:g} km ({km_to_mi(s.km):.1f} miles). {minutes(est_minutes(plan, it))}.",
            f"How it should feel: {feel(s.pace)}.",
            f"Pace: {p['km'].replace('/km', ' per km')} ({p['mi'].replace('/mi', ' per mile')})",
            f"On a treadmill: {p['kmh']} ({p['mph']})",
        ]
        if p["hr"]:
            lines.append(f"Heart rate: {p['hr']}")
        if s.pace != "E":
            lines.append(f"Easy parts: {e['km'].replace('/km', ' per km')} ({e['mi'].replace('/mi', ' per mile')})")
        if s.detail:
            lines.append(f"The session: {s.detail}")
        if s.type in ("workout", "test") and plan.warmup:
            lines.append(f"Warm up: {plan.warmup}. Cool down: {plan.cooldown or 'easy'}.")
        if s.strides and plan.strides:
            lines.append(f"Strides: {s.strides} times ({plan.strides})")
    for a in it.adjustments:
        lines.append("")
        lines.append(("Question for you: " if a.kind == "ask" else "Why this changed: ") + a.reason)
        if a.rule in RULES:
            r = RULES[a.rule]
            lines.append(f"Rule: {r.name}. How sure we are: {r.level_text}.")
    week = next((w for w in plan.weeks if w.n == it.week), None)
    lines.append("")
    lines.append(f"Week {it.week} of {len(plan.weeks)}" + (f" ({week.note})" if week and week.note else ""))
    lines.append("Not medical advice. If you feel pain or feel sick, do not run.")
    lines.append("runcoach")
    return "\n".join(lines)


def one_line(plan: Plan, it: Scheduled) -> str:
    if it.dropped:
        return f"{it.original_date:%a %d %b}: dropped {it.session.type} {it.session.km:g} km"
    return f"{it.date:%a %d %b}: {title(plan, it)}"
