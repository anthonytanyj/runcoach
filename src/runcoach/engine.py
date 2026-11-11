"""The coach: plan + your state -> today's run, the week, and changes with before/after."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

from .adapt import Rules, adapt, mark_status
from .models import BlockEvent, ChangeKind, LifeChange, Plan, Scheduled
from .paces import fmt_pace, parse_pace
from .render import one_line
from .scheduler import all_block_events, schedule
from .store import Store


def fmt_time(minutes: float) -> str:
    secs = round(minutes * 60)
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


@dataclass
class Diff:
    change: LifeChange
    moved: list[tuple[Scheduled, Scheduled]] = field(default_factory=list)
    dropped: list[Scheduled] = field(default_factory=list)
    restored: list[Scheduled] = field(default_factory=list)
    other: list[tuple[Scheduled, Scheduled]] = field(default_factory=list)
    lifts_skipped: list[BlockEvent] = field(default_factory=list)
    last_week: int = 0

    @property
    def empty(self) -> bool:
        return not (self.moved or self.dropped or self.restored or self.other or self.lifts_skipped)

    @property
    def needs_ok(self) -> bool:
        """Big changes must be confirmed by the runner before they are applied.

        Small (the agent may do and then tell): move a run 1 day, drop an easy run.
        Big (ask first): drop a long run or workout, move a run 2+ days,
        anything in race week, or illness.
        """
        far_move = any(abs((a.date - b.date).days) > 1 for b, a in self.moved)
        big_drop = any(d.session.type in ("long", "race", "workout", "test") for d in self.dropped)
        touched = [a for _, a in self.moved] + self.dropped + [a for _, a in self.other]
        race_week = any(x.week == self.last_week for x in touched)
        return far_move or big_drop or race_week or self.change.kind == ChangeKind.ill

    def summary(self, plan: Plan) -> list[str]:
        from .render import short_title

        lines = []
        for b, a in self.moved:
            lines.append(f"Move: {short_title(plan, b)}, {b.date:%a %-d %b} → {a.date:%a %-d %b}")
        for d in self.dropped:
            lines.append(f"Drop: {d.session.type.capitalize()} {d.session.km:g} km on {d.original_date:%a %-d %b}")
        for r in self.restored:
            lines.append(f"Back in plan: {one_line(plan, r)}")
        for b, a in self.other:
            lines.append(f"Change: {one_line(plan, b)} → {one_line(plan, a)}")
        for e in self.lifts_skipped:
            lines.append(f"Skip lift: {e.block.name} on {e.date:%a %-d %b}")
        return lines or ["No change: your runs stay the same."]


def scale_paces(plan: Plan, factor: float) -> Plan:
    """Make every pace faster (factor < 1) or slower (factor > 1)."""
    if abs(factor - 1) < 1e-6:
        return plan
    paces = {
        k: p.model_copy(update={"min_per_km": [fmt_pace(parse_pace(x) * factor) for x in p.min_per_km]})
        for k, p in plan.paces.items()
    }
    return plan.model_copy(update={"paces": paces})


def riegel(minutes: float, km: float, to_km: float) -> float:
    """Predict a race time from another race (Riegel 1981). Good for 5K to half marathon."""
    return minutes * (to_km / km) ** 1.06


class Coach:
    def __init__(self, plan: Plan, store: Store, rules: Rules | None = None):
        self.base_plan = plan
        self.store = store
        self.rules = rules or Rules()

    @property
    def plan(self) -> Plan:
        f = self.store.state.pace_factor
        if getattr(self, "_scaled_for", None) != f:
            self._scaled = scale_paces(self.base_plan, f)
            self._scaled_for = f
        return self._scaled

    # --- paces from your own results -------------------------------------
    def propose_paces(self, km: float, minutes: float) -> dict:
        """From a test or race result, propose new paces. Nothing changes until accept_paces()."""
        hm = riegel(minutes, km, 21.0975)
        base_hmp = parse_pace(self.base_plan.paces["HMP"].min_per_km[0])
        new_hmp = hm / 21.0975
        factor = max(0.85, min(1.15, new_hmp / base_hmp))
        now_hmp = base_hmp * self.store.state.pace_factor
        h, m = divmod(round(hm), 60)
        faster = new_hmp < now_hmp
        same = abs(new_hmp - now_hmp) < 3 / 60
        text = (
            f"Your {km:g} km in {fmt_time(minutes)} suggests a half marathon in about {h}:{m:02d}. "
            + ("Your paces already match this result." if same else
               f"Your race pace would be {fmt_pace(new_hmp)} per km instead of {fmt_pace(now_hmp)}. "
               f"All your paces would be {'faster' if faster else 'slower'} by the same amount.")
        )
        return {"factor": round(factor, 4), "half_marathon_minutes": round(hm, 1), "race_pace": fmt_pace(new_hmp),
                "current_race_pace": fmt_pace(now_hmp), "same": same, "text": text, "rule": "pace_from_test"}

    def accept_paces(self, proposal: dict) -> None:
        self.store.set_pace_factor(proposal["factor"], f"New paces from your result: race pace {proposal['race_pace']} per km")

    # --- weekly load (effort x minutes) and planned vs done -----------------
    def week_summary(self, n: int, today: dt.date | None = None) -> dict:
        today = today or dt.date.today()
        start = self.plan.week_start(n)
        end = start + dt.timedelta(days=6)
        st = self.store.state
        items = [s for s in self.sessions(today) if s.week == n]
        planned = sum(s.session.km for s in items if not s.dropped and s.status != "rest")
        runs = [a for a in st.activities if a.is_run and start <= a.date <= end]
        done = round(sum(a.km or 0 for a in runs), 1)

        def load(a_start, a_end):
            total, any_ = 0.0, False
            for a in st.activities:
                if a_start <= a.date <= a_end and a.minutes:
                    eff = (st.feelings.get(a.date) or {}).get("effort")
                    if eff:
                        total += eff * a.minutes
                        any_ = True
            return total if any_ else None

        this = load(start, end)
        prev = [load(start - dt.timedelta(weeks=k), end - dt.timedelta(weeks=k)) for k in range(1, 5)]
        prev = [x for x in prev if x]
        feel = None
        if this and len(prev) >= 2:
            avg = sum(prev) / len(prev)
            ratio = this / avg
            feel = ("This week was a lot more tiring than your usual week." if ratio > 1.3 else
                    "This week was lighter than your usual week." if ratio < 0.7 else
                    "This week was about as tiring as usual.")
        past = end < today
        if planned and (past or done):
            share = done / planned
            words = (f"You ran {done:g} of {planned:g} planned km." if done else f"No runs logged yet. {planned:g} km planned.")
            if past and share < 0.6:
                words += " That is fine. We do not add missed km to next week."
        else:
            words = f"{planned:g} km planned."
        return {"planned_km": planned, "done_km": done, "load": this, "words": words, "feel": feel}

    @classmethod
    def from_files(cls, plan_path: str | Path, state_path: str | Path | None = None) -> "Coach":
        return cls(Plan.load(plan_path), Store(state_path))

    # --- views -----------------------------------------------------------
    def sessions(self, today: dt.date | None = None, changes: list[LifeChange] | None = None) -> list[Scheduled]:
        today = today or dt.date.today()
        st = self.store.state
        changes = st.changes if changes is None else changes
        items = schedule(self.plan, changes, not_before=today, moves=st.moves)
        items = adapt(self.plan, items, st.metrics, st.activities, changes, st.feelings, self.rules,
                      pains=st.pains, overrides=st.overrides)
        return mark_status(items, st.activities, today)

    def blocks(self, changes: list[LifeChange] | None = None) -> list[BlockEvent]:
        return all_block_events(self.plan, self.store.state.changes if changes is None else changes)

    def on(self, day: dt.date, today: dt.date | None = None) -> list[Scheduled]:
        return [s for s in self.sessions(today or day) if s.date == day and not s.dropped]

    def today(self, today: dt.date | None = None) -> dict:
        today = today or dt.date.today()
        items = self.sessions(today)
        todays = [s for s in items if s.date == today and not s.dropped]
        upcoming = [s for s in items if s.date > today and not s.dropped][:3]
        lifts = [b for b in self.blocks() if b.date == today and not b.skipped_reason]
        st = self.store.state
        m = next((m for m in st.metrics if m.date == today), None)
        from .words import recovery

        return {"date": today, "week": self.plan.week_of(today), "runs": todays, "lifts": lifts,
                "next": upcoming, "metrics": m, "recovery": recovery(self.plan, today, st.metrics),
                "pain_today": next((p for p in st.pains if p.date == today), None),
                "feeling": st.feelings.get(today, {})}

    def week(self, n: int, today: dt.date | None = None) -> list[Scheduled]:
        return [s for s in self.sessions(today) if s.week == n]

    # --- life changes ------------------------------------------------------
    def preview(self, change: LifeChange, today: dt.date | None = None) -> Diff:
        today = today or dt.date.today()
        before = {s.key: s for s in self.sessions(today)}
        after = {s.key: s for s in self.sessions(today, [*self.store.state.changes, change])}
        diff = Diff(change=change, last_week=len(self.plan.weeks))
        for k, a in after.items():
            b = before[k]
            if a.date < today and b.date < today:
                continue
            if a.dropped and not b.dropped:
                diff.dropped.append(a)
            elif b.dropped and not a.dropped:
                diff.restored.append(a)
            elif a.date != b.date:
                diff.moved.append((b, a))
            elif a.session != b.session:
                diff.other.append((b, a))
        b_lifts = {(e.date, e.block.name): e for e in self.blocks()}
        for e in self.blocks([*self.store.state.changes, change]):
            if e.skipped_reason and not b_lifts[(e.date, e.block.name)].skipped_reason and e.date >= today:
                diff.lifts_skipped.append(e)
        diff.moved.sort(key=lambda x: x[0].date)
        diff.dropped.sort(key=lambda x: x.original_date)
        return diff

    def apply(self, change: LifeChange, today: dt.date | None = None) -> Diff:
        diff = self.preview(change, today)
        label = f"{change.kind.value} {change.start:%d %b}" + (
            f"-{change.end:%d %b}" if change.end != change.start else ""
        ) + (f": {change.note}" if change.note else "")
        self.store.add_change(change, label)
        return diff

    def undo(self):
        return self.store.undo_last()
