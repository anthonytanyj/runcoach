"""Garmin input (read only). Needs ``pip install runcoach[garmin]``.

This uses the unofficial ``garminconnect`` library with your own Garmin login.
It is not an official Garmin API, and Garmin can break it at any time. That is
why runcoach also takes data in other ways: the MCP tools ``log_metrics`` and
``log_activity`` (an AI assistant with its own Garmin connector can call them),
and the web app.

Login: set GARMIN_EMAIL and GARMIN_PASSWORD once. The session tokens are saved
in ``~/.runcoach/garmin`` and used again, so the password is not needed each time.
Runcoach never writes to Garmin.
"""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path
from typing import Any

from .models import Activity, DayMetrics


def _first(d: Any, *keys: str):
    """Return the first non-empty value for any of the keys (looks one level deep in lists)."""
    if isinstance(d, list):
        d = d[0] if d else {}
    if not isinstance(d, dict):
        return None
    for k in keys:
        cur: Any = d
        for part in k.split("."):
            if isinstance(cur, list) and part.isdigit():
                cur = cur[int(part)] if int(part) < len(cur) else None
            else:
                cur = cur.get(part) if isinstance(cur, dict) else None
            if cur is None:
                break
        if cur not in (None, ""):
            return cur
    return None


class GarminSource:
    def __init__(self, token_dir: str | Path | None = None):
        self.token_dir = Path(token_dir or Path.home() / ".runcoach" / "garmin")
        self._api = None

    def api(self):
        if self._api:
            return self._api
        from garminconnect import Garmin

        self.token_dir.mkdir(parents=True, exist_ok=True)
        try:
            api = Garmin()
            api.login(str(self.token_dir))
        except Exception:
            email, pw = os.environ.get("GARMIN_EMAIL"), os.environ.get("GARMIN_PASSWORD")
            if not (email and pw):
                raise RuntimeError("Set GARMIN_EMAIL and GARMIN_PASSWORD for the first login.")
            api = Garmin(email, pw)
            api.login()
            api.garth.dump(str(self.token_dir))
        self._api = api
        return api

    def metrics(self, day: dt.date) -> DayMetrics:
        api = self.api()
        iso = day.isoformat()
        m = DayMetrics(date=day)
        try:
            m.readiness = _first(api.get_training_readiness(iso), "score")
        except Exception:
            pass
        try:
            sleep = api.get_sleep_data(iso)
            secs = _first(sleep, "dailySleepDTO.sleepTimeSeconds")
            m.sleep_hours = round(secs / 3600, 1) if secs else None
            m.rhr = _first(sleep, "restingHeartRate")
            m.hrv = _first(sleep, "avgOvernightHrv")
        except Exception:
            pass
        if m.rhr is None:
            try:
                m.rhr = _first(api.get_rhr_day(iso), "allMetrics.metricsMap.WELLNESS_RESTING_HEART_RATE.0.value")
            except Exception:
                pass
        if m.hrv is None:
            try:
                m.hrv = _first(api.get_hrv_data(iso), "hrvSummary.lastNightAvg")
            except Exception:
                pass
        return m

    def activities(self, start: dt.date, end: dt.date) -> list[Activity]:
        raw = self.api().get_activities_by_date(start.isoformat(), end.isoformat())
        out = []
        for a in raw or []:
            t = (_first(a, "activityType.typeKey") or "other").lower()
            out.append(
                Activity(
                    date=dt.date.fromisoformat(str(a.get("startTimeLocal", ""))[:10]),
                    type=t,
                    km=round((a.get("distance") or 0) / 1000, 2) or None,
                    minutes=round((a.get("duration") or 0) / 60, 1) or None,
                    ascent_m=a.get("elevationGain"),
                    avg_hr=a.get("averageHR"),
                    name=a.get("activityName"),
                )
            )
        return out


def sync(store, days: int = 14, today: dt.date | None = None, source: GarminSource | None = None) -> dict:
    """Pull the last ``days`` of activities and the last 3 days of body data into the store."""
    today = today or dt.date.today()
    src = source or GarminSource()
    acts = src.activities(today - dt.timedelta(days=days), today)
    added = store.add_activities(acts)
    mets = [src.metrics(today - dt.timedelta(days=i)) for i in range(3)]
    store.upsert_metrics(mets)
    return {"activities_added": added, "metrics_days": len(mets), "today": mets[0].model_dump(mode="json")}
