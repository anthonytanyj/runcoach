"""Data models for plans, life changes, body data, and the scheduled result."""

from __future__ import annotations

import datetime as dt
from enum import Enum
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
Slot = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

SessionType = Literal["race", "long", "workout", "test", "easy"]

# Low number = more important = kept first when days are lost.
PRIORITY: dict[str, int] = {"race": 0, "long": 1, "workout": 2, "test": 2, "easy": 3}
HARD_TYPES = {"race", "long", "workout", "test"}


class Pace(BaseModel):
    name: str
    min_per_km: list[str]
    hr: str | None = None


class Session(BaseModel):
    slot: Slot
    type: SessionType
    km: float
    pace: str = "E"
    title: str | None = None
    detail: str | None = None
    strides: int = 0
    optional: bool = False

    @property
    def priority(self) -> int:
        return PRIORITY[self.type]

    @property
    def hard(self) -> bool:
        return self.type in HARD_TYPES


class Week(BaseModel):
    n: int
    note: str | None = None
    sessions: list[Session]


class Block(BaseModel):
    """A fixed non-run session, for example strength training."""

    name: str
    days: list[Slot]
    legs: bool = False
    time: str = "18:00"
    minutes: int = 45
    until: dt.date | None = None


class Race(BaseModel):
    name: str
    date: dt.date
    goal: str | None = None


class Athlete(BaseModel):
    run_time: str = "07:00"
    long_run_time: str = "08:00"
    timezone: str = "UTC"
    hrv_baseline: float | None = None
    rhr_normal: tuple[int, int] | None = None
    easy_hr: tuple[int, int] | None = None
    easy_hr_cap: int | None = None


class Plan(BaseModel):
    format: str = "runcoach-plan/1"
    name: str
    race: Race
    start: dt.date
    athlete: Athlete = Field(default_factory=Athlete)
    blocks: list[Block] = Field(default_factory=list)
    paces: dict[str, Pace]
    warmup: str | None = None
    cooldown: str | None = None
    strides: str | None = None
    weeks: list[Week]

    @classmethod
    def load(cls, path: str | Path) -> "Plan":
        with open(path, encoding="utf-8") as f:
            return cls.model_validate(yaml.safe_load(f))

    def week_start(self, n: int) -> dt.date:
        return self.start + dt.timedelta(weeks=n - 1)

    def week_of(self, day: dt.date) -> int | None:
        n = (day - self.start).days // 7 + 1
        return n if 1 <= n <= len(self.weeks) else None

    @property
    def end(self) -> dt.date:
        return self.week_start(len(self.weeks)) + dt.timedelta(days=6)


class ChangeKind(str, Enum):
    away = "away"            # no runs possible (trip, wedding, busy days)
    treadmill = "treadmill"  # runs possible, but only on a treadmill
    ill = "ill"              # sick: no runs, and easy return after
    busy = "busy"            # one day is too full; same as away for runs


class LifeChange(BaseModel):
    """A change in your life that the plan must work around."""

    id: str | None = None
    start: dt.date
    end: dt.date
    kind: ChangeKind = ChangeKind.away
    note: str = ""
    lifts_ok: bool = False   # True if strength blocks still happen (e.g. hotel gym)

    def covers(self, day: dt.date) -> bool:
        return self.start <= day <= self.end

    @property
    def blocks_runs(self) -> bool:
        return self.kind in (ChangeKind.away, ChangeKind.ill, ChangeKind.busy)


class DayMetrics(BaseModel):
    """Body data for one day (usually from the watch, measured that morning)."""

    date: dt.date
    readiness: float | None = None
    rhr: float | None = None
    hrv: float | None = None
    sleep_hours: float | None = None


class Activity(BaseModel):
    """A finished activity (run, hike, surf, lift, ...)."""

    date: dt.date
    type: str
    km: float | None = None
    minutes: float | None = None
    ascent_m: float | None = None
    avg_hr: float | None = None
    name: str | None = None

    @property
    def is_run(self) -> bool:
        return "run" in self.type.lower()


class Adjustment(BaseModel):
    """A change the coach made to one session (or a question it asks), with the reason."""

    kind: Literal["moved", "dropped", "easier", "no_run", "treadmill", "ask", "kept", "note"]
    reason: str
    rule: str | None = None   # id in runcoach.rules


class Scheduled(BaseModel):
    """One session placed on one date, after scheduling and adaptation."""

    date: dt.date
    week: int
    session: Session
    original_date: dt.date
    adjustments: list[Adjustment] = Field(default_factory=list)
    status: Literal["planned", "done", "missed", "dropped", "rest"] = "planned"

    @property
    def dropped(self) -> bool:
        return self.status == "dropped"

    @property
    def key(self) -> str:
        """Stable id for this session in the plan (does not change when it moves)."""
        return f"w{self.week}-{self.session.slot}-{self.session.type}"


class PainReport(BaseModel):
    date: dt.date
    where: str = ""
    score: int = 0                # 0 = none, 10 = worst
    changed_stride: bool = False  # "did it change how you run?"

    @property
    def stop(self) -> bool:
        return self.changed_stride or self.score >= 4


class BlockEvent(BaseModel):
    date: dt.date
    block: Block
    skipped_reason: str | None = None


def slot_index(slot: str) -> int:
    return DAYS.index(slot)
