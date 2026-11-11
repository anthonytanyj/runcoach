"""Local state: life changes, body data, activities, feelings, and the change log.

Everything is in one JSON file on your own computer (default
``~/.runcoach/state.json``). Nothing is sent anywhere.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import uuid
from pathlib import Path

from pydantic import BaseModel, Field

from .models import Activity, DayMetrics, LifeChange, PainReport


class LogEntry(BaseModel):
    at: dt.datetime
    action: str
    summary: str
    change_id: str | None = None
    undone: bool = False


class State(BaseModel):
    changes: list[LifeChange] = Field(default_factory=list)
    metrics: list[DayMetrics] = Field(default_factory=list)
    activities: list[Activity] = Field(default_factory=list)
    feelings: dict[dt.date, dict] = Field(default_factory=dict)
    log: list[LogEntry] = Field(default_factory=list)
    pains: list[PainReport] = Field(default_factory=list)
    overrides: list[str] = Field(default_factory=list)   # "YYYY-MM-DD|session key": keep the original run
    pace_factor: float = 1.0                               # 1.0 = plan paces; 0.97 = 3% faster


def default_path() -> Path:
    return Path(os.environ.get("RUNCOACH_STATE", Path.home() / ".runcoach" / "state.json"))


class Store:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else default_path()
        self.state = self._load()

    def _load(self) -> State:
        if self.path.exists():
            return State.model_validate_json(self.path.read_text(encoding="utf-8"))
        return State()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(self.state.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(self.path)

    # --- life changes -------------------------------------------------
    def add_change(self, change: LifeChange, summary: str) -> LifeChange:
        change = change.model_copy(update={"id": change.id or uuid.uuid4().hex[:8]})
        self.state.changes.append(change)
        self.state.log.append(
            LogEntry(at=dt.datetime.now(dt.timezone.utc), action="change", summary=summary, change_id=change.id)
        )
        self.save()
        return change

    def undo_last(self) -> LogEntry | None:
        for entry in reversed(self.state.log):
            if entry.action == "change" and not entry.undone:
                self.state.changes = [c for c in self.state.changes if c.id != entry.change_id]
                entry.undone = True
                self.state.log.append(
                    LogEntry(at=dt.datetime.now(dt.timezone.utc), action="undo", summary=f"Undid: {entry.summary}")
                )
                self.save()
                return entry
        return None

    # --- data in --------------------------------------------------------
    def upsert_metrics(self, items: list[DayMetrics]) -> None:
        by_day = {m.date: m for m in self.state.metrics}
        for m in items:
            old = by_day.get(m.date)
            if old:
                merged = old.model_dump() | {k: v for k, v in m.model_dump().items() if v is not None}
                by_day[m.date] = DayMetrics.model_validate(merged)
            else:
                by_day[m.date] = m
        self.state.metrics = sorted(by_day.values(), key=lambda m: m.date)
        self.save()

    def add_activities(self, items: list[Activity]) -> int:
        seen = {(a.date, a.type, a.km, a.minutes) for a in self.state.activities}
        new = [a for a in items if (a.date, a.type, a.km, a.minutes) not in seen]
        self.state.activities = sorted(self.state.activities + new, key=lambda a: a.date)
        self.save()
        return len(new)

    def add_pain(self, report: PainReport) -> None:
        self.state.pains = [p for p in self.state.pains if p.date != report.date] + [report]
        self.state.pains.sort(key=lambda p: p.date)
        self.state.log.append(LogEntry(at=_now(), action="pain",
                                       summary=f"Pain check {report.date:%-d %b}: {report.score}/10 {report.where}".strip()))
        self.save()

    def keep_original(self, day: dt.date, key: str) -> None:
        tag = f"{day.isoformat()}|{key}"
        if tag not in self.state.overrides:
            self.state.overrides.append(tag)
            self.state.log.append(LogEntry(at=_now(), action="override", summary=f"Kept the original run on {day:%a %-d %b}"))
            self.save()

    def set_pace_factor(self, factor: float, why: str) -> None:
        self.state.pace_factor = factor
        self.state.log.append(LogEntry(at=_now(), action="paces", summary=why))
        self.save()

    def set_feeling(self, day: dt.date, effort: int | None = None, legs: str | None = None, note: str | None = None,
                    ready: str | None = None) -> None:
        """effort 1-10 for the run that day; legs fresh/ok/heavy; ready fine/tired (answer to "how do you feel?")."""
        f = dict(self.state.feelings.get(day, {}))
        f.update({k: v for k, v in {"effort": effort, "legs": legs, "note": note, "ready": ready}.items() if v is not None})
        self.state.feelings[day] = f
        self.save()


def _now():
    return dt.datetime.now(dt.timezone.utc)


def dumps(obj) -> str:
    return json.dumps(obj, default=str, indent=2)
