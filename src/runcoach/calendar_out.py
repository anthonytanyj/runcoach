"""Calendar output.

Three ways to get the plan into a calendar:

1. ``events()``: plain event data (title, start, end, description, a stable id).
   An AI agent with its own calendar connector can write these. This is the
   simplest path when runcoach runs as an MCP server inside an assistant.
2. ``to_ics()``: an .ics file or feed for any calendar app (Apple, Outlook, Google).
   Note: Google refreshes subscribed feeds only every 12-24 h.
3. ``GoogleCalendarSync``: two-way sync with a separate "Training" calendar in your
   Google account. It reads the runs you moved or deleted in the calendar app and
   keeps your choice, then writes the plan. It only touches events that it made.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path
from zoneinfo import ZoneInfo

from .engine import Coach
from .models import BlockEvent, Move, Plan, Scheduled
from .render import description, est_minutes, start_time, title

CALENDAR_NAME = "Training"
MARK = "runcoach"


def _uid(key: str) -> str:
    # Google event ids: lowercase base32hex (a-v, 0-9), 5-1024 chars.
    return "rc" + hashlib.sha1(key.encode()).hexdigest()


def _hm(t: str) -> dt.time:
    h, m = t.split(":")
    return dt.time(int(h), int(m))


def run_event(plan: Plan, it: Scheduled, move: Move | None = None) -> dict:
    tz = ZoneInfo(plan.athlete.timezone)
    at = _hm(move.time) if move and move.time and move.date == it.date else start_time(plan, it)
    start = dt.datetime.combine(it.date, at, tzinfo=tz)
    end = start + dt.timedelta(minutes=est_minutes(plan, it))
    return {
        "id": _uid(it.key),
        "key": it.key,
        "title": title(plan, it),
        "description": description(plan, it),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "timezone": plan.athlete.timezone,
        "kind": "run",
        "changed": bool(it.adjustments),
    }


def block_event(plan: Plan, e: BlockEvent, move: Move | None = None) -> dict:
    tz = ZoneInfo(plan.athlete.timezone)
    day = move.date if move and move.date else e.date
    at = _hm(move.time) if move and move.time else _hm(e.block.time)
    start = dt.datetime.combine(day, at, tzinfo=tz)
    end = start + dt.timedelta(minutes=e.block.minutes)
    key = e.key
    return {
        "id": _uid(key),
        "key": key,
        "title": e.block.name,
        "description": "Strength block from your plan.\nruncoach",
        "start": start.isoformat(),
        "end": end.isoformat(),
        "timezone": plan.athlete.timezone,
        "kind": "block",
        "changed": False,
    }


def events(
    coach: Coach,
    start: dt.date | None = None,
    end: dt.date | None = None,
    today: dt.date | None = None,
    include_blocks: bool = True,
) -> list[dict]:
    today = today or dt.date.today()
    start = start or today
    end = end or coach.plan.race.date
    moves = coach.store.state.moves
    out = [
        run_event(coach.plan, s, moves.get(s.key))
        for s in coach.sessions(today)
        if not s.dropped and start <= s.date <= end
    ]
    if include_blocks:
        for b in coach.blocks():
            mv = moves.get(b.key)
            if b.skipped_reason or (mv and mv.removed):
                continue
            ev = block_event(coach.plan, b, mv)
            if start.isoformat() <= ev["start"][:10] <= end.isoformat():
                out.append(ev)
    return sorted(out, key=lambda e: e["start"])


def _ics_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _ics_time(iso: str) -> str:
    return dt.datetime.fromisoformat(iso).astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _fold(line: str) -> str:
    out, cur = [], line
    while len(cur.encode()) > 74:
        cut = 74
        while len(cur[:cut].encode()) > 74:
            cut -= 1
        out.append(cur[:cut])
        cur = " " + cur[cut:]
    out.append(cur)
    return "\r\n".join(out)


def to_ics(evts: list[dict], name: str = CALENDAR_NAME) -> str:
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//runcoach//EN",
        "CALSCALE:GREGORIAN",
        f"X-WR-CALNAME:{name}",
    ]
    for e in evts:
        lines += [
            "BEGIN:VEVENT",
            f"UID:{e['id']}@runcoach",
            f"DTSTAMP:{now}",
            f"DTSTART:{_ics_time(e['start'])}",
            f"DTEND:{_ics_time(e['end'])}",
            f"SUMMARY:{_ics_escape(e['title'])}",
            f"DESCRIPTION:{_ics_escape(e['description'])}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(x) for x in lines) + "\r\n"


class GoogleCalendarSync:
    """Write events to a separate Google calendar. Needs ``pip install runcoach[google]``.

    Setup (one time): make an OAuth client of type "Desktop app" in Google Cloud,
    enable the Google Calendar API, and save the file as ``credentials.json``
    in ``~/.runcoach/``. The first sync opens a browser to sign in.
    """

    SCOPES = ["https://www.googleapis.com/auth/calendar"]

    def __init__(self, folder: str | Path | None = None, calendar_name: str = CALENDAR_NAME):
        self.folder = Path(folder or Path.home() / ".runcoach")
        self.calendar_name = calendar_name
        self._svc = None

    def _service(self):
        if self._svc:
            return self._svc
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build

        token = self.folder / "token.json"
        creds = Credentials.from_authorized_user_file(token, self.SCOPES) if token.exists() else None
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(self.folder / "credentials.json", self.SCOPES)
                creds = flow.run_local_server(port=0)
            token.write_text(creds.to_json())
        self._svc = build("calendar", "v3", credentials=creds, cache_discovery=False)
        return self._svc

    def calendar_id(self) -> str:
        svc = self._service()
        page = None
        while True:
            res = svc.calendarList().list(pageToken=page).execute()
            for c in res.get("items", []):
                if c.get("summary") == self.calendar_name:
                    return c["id"]
            page = res.get("nextPageToken")
            if not page:
                break
        cal = svc.calendars().insert(body={"summary": self.calendar_name}).execute()
        return cal["id"]

    def _list(self, cal: str, start: dt.date, end: dt.date, show_deleted: bool = False) -> list[dict]:
        svc = self._service()
        tmin = dt.datetime.combine(start, dt.time.min, tzinfo=dt.timezone.utc).isoformat()
        tmax = dt.datetime.combine(end + dt.timedelta(days=1), dt.time.min, tzinfo=dt.timezone.utc).isoformat()
        out: list[dict] = []
        page = None
        while True:
            res = svc.events().list(
                calendarId=cal, timeMin=tmin, timeMax=tmax, pageToken=page,
                privateExtendedProperty=f"{MARK}=1", singleEvents=True, showDeleted=show_deleted,
            ).execute()
            out.extend(res.get("items", []))
            page = res.get("nextPageToken")
            if not page:
                return out

    def pull(self, coach: Coach, today: dt.date | None = None) -> list[str]:
        """Read changes the runner made in the calendar app and save them as moves.

        runcoach writes the start time it chose into each event (``rc_start``). If the
        event now starts at another time, the runner moved it. If the runner deleted
        an event that runcoach did not delete, the run is removed from the plan.
        """
        today = today or dt.date.today()
        cal = self.calendar_id()
        tz = ZoneInfo(coach.plan.athlete.timezone)
        store = coach.store
        wanted = {e["key"] for e in events(coach, today - dt.timedelta(days=7), coach.plan.race.date, today)}
        found: list[str] = []
        for ev in self._list(cal, today - dt.timedelta(days=7), coach.plan.race.date, show_deleted=True):
            props = (ev.get("extendedProperties") or {}).get("private") or {}
            key = props.get("key")
            if not key:
                continue
            name = ev.get("summary") or key
            if ev.get("status") == "cancelled":
                if ev["id"] in store.state.cal_deleted or key not in wanted:
                    continue
                if store.set_move(key, Move(date=None), f"You removed {name} from your calendar."):
                    found.append(f"You removed {name} from your calendar.")
                continue
            planned = props.get("rc_start")
            st = ev.get("start") or {}
            if not planned or not (st.get("dateTime") or st.get("date")):
                continue
            was = dt.datetime.fromisoformat(planned).astimezone(tz)
            if "dateTime" in st:
                now = dt.datetime.fromisoformat(st["dateTime"].replace("Z", "+00:00")).astimezone(tz)
                if now == was:
                    continue
                move = Move(date=now.date(), time=f"{now:%H:%M}")
                words = f"You moved {name} to {now:%a %-d %b} at {now:%H:%M}."
            else:  # changed to an all-day event: keep the new day, use the plan's time
                day = dt.date.fromisoformat(st["date"])
                if day == was.date():
                    continue
                move = Move(date=day)
                words = f"You moved {name} to {day:%a %-d %b}."
            if store.set_move(key, move, words):
                found.append(words)
        return found

    def push(self, evts: list[dict], start: dt.date, end: dt.date, store=None) -> dict:
        """Make the calendar match ``evts``. Only runcoach events are changed; deletes stay in [start, end]."""
        svc = self._service()
        cal = self.calendar_id()
        lo = min([start] + [dt.date.fromisoformat(e["start"][:10]) for e in evts])
        hi = max([end] + [dt.date.fromisoformat(e["start"][:10]) for e in evts])
        existing = {e["id"]: e for e in self._list(cal, lo, hi)}
        deleted_ids = store.state.cal_deleted if store is not None else []

        created = updated = deleted = 0
        want = {e["id"]: e for e in evts}
        for eid, e in want.items():
            body = {
                "id": eid,
                "status": "confirmed",
                "summary": e["title"],
                "description": e["description"],
                "start": {"dateTime": e["start"], "timeZone": e["timezone"]},
                "end": {"dateTime": e["end"], "timeZone": e["timezone"]},
                "extendedProperties": {"private": {MARK: "1", "key": e["key"], "rc_start": e["start"]}},
            }
            old = existing.get(eid)
            if old is None:
                try:
                    svc.events().insert(calendarId=cal, body=body).execute()
                except Exception:  # id used before by a deleted event: update it instead
                    svc.events().update(calendarId=cal, eventId=eid, body=body).execute()
                if eid in deleted_ids:
                    deleted_ids.remove(eid)
                created += 1
            elif (
                old.get("summary") != body["summary"]
                or old.get("description") != body["description"]
                or ((old.get("extendedProperties") or {}).get("private") or {}).get("rc_start") != e["start"]
                or old["start"].get("dateTime", "")[:16] != e["start"][:16]
            ):
                svc.events().update(calendarId=cal, eventId=eid, body=body).execute()
                updated += 1
        for eid, old in existing.items():
            if eid in want:
                continue
            day = (old.get("start") or {}).get("dateTime", "")[:10] or (old.get("start") or {}).get("date", "")
            if day and not (start.isoformat() <= day <= end.isoformat()):
                continue
            svc.events().delete(calendarId=cal, eventId=eid).execute()
            if eid not in deleted_ids:
                deleted_ids.append(eid)
            deleted += 1
        if store is not None:
            store.save()
        return {"created": created, "updated": updated, "deleted": deleted, "calendar": self.calendar_name}

    def sync(self, coach: Coach, start: dt.date, end: dt.date, today: dt.date | None = None) -> dict:
        """Two-way sync: first read the runner's own moves, then write the plan."""
        today = today or dt.date.today()
        moved = self.pull(coach, today)
        result = self.push(events(coach, start, end, today), start, end, coach.store)
        return {**result, "your_moves": moved}
