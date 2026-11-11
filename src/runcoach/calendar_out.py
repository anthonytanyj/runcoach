"""Calendar output.

Three ways to get the plan into a calendar:

1. ``events()``: plain event data (title, start, end, description, a stable id).
   An AI agent with its own calendar connector can write these. This is the
   simplest path when runcoach runs as an MCP server inside an assistant.
2. ``to_ics()``: an .ics file or feed for any calendar app (Apple, Outlook, Google).
   Note: Google refreshes subscribed feeds only every 12-24 h.
3. ``GoogleCalendarSync``: writes to a separate "Training" calendar in your Google
   account through the Google Calendar API. It only touches events that it made.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path
from zoneinfo import ZoneInfo

from .engine import Coach
from .models import BlockEvent, Plan, Scheduled
from .render import description, est_minutes, start_time, title

CALENDAR_NAME = "Training"
MARK = "runcoach"


def _uid(key: str) -> str:
    # Google event ids: lowercase base32hex (a-v, 0-9), 5-1024 chars.
    return "rc" + hashlib.sha1(key.encode()).hexdigest()


def run_event(plan: Plan, it: Scheduled) -> dict:
    tz = ZoneInfo(plan.athlete.timezone)
    start = dt.datetime.combine(it.date, start_time(plan, it), tzinfo=tz)
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


def block_event(plan: Plan, e: BlockEvent) -> dict:
    tz = ZoneInfo(plan.athlete.timezone)
    h, m = e.block.time.split(":")
    start = dt.datetime.combine(e.date, dt.time(int(h), int(m)), tzinfo=tz)
    end = start + dt.timedelta(minutes=e.block.minutes)
    key = f"block-{e.block.name}-{e.date.isoformat()}"
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
    out = [
        run_event(coach.plan, s)
        for s in coach.sessions(today)
        if not s.dropped and start <= s.date <= end
    ]
    if include_blocks:
        out += [
            block_event(coach.plan, b)
            for b in coach.blocks()
            if not b.skipped_reason and start <= b.date <= end
        ]
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

    def sync(self, evts: list[dict], start: dt.date, end: dt.date) -> dict:
        """Make the calendar match ``evts`` between start and end. Only runcoach events are changed."""
        svc = self._service()
        cal = self.calendar_id()
        tmin = dt.datetime.combine(start, dt.time.min, tzinfo=dt.timezone.utc).isoformat()
        tmax = dt.datetime.combine(end + dt.timedelta(days=1), dt.time.min, tzinfo=dt.timezone.utc).isoformat()
        existing: dict[str, dict] = {}
        page = None
        while True:
            res = svc.events().list(
                calendarId=cal, timeMin=tmin, timeMax=tmax, pageToken=page,
                privateExtendedProperty=f"{MARK}=1", singleEvents=True,
            ).execute()
            for e in res.get("items", []):
                existing[e["id"]] = e
            page = res.get("nextPageToken")
            if not page:
                break

        created = updated = deleted = 0
        want = {e["id"]: e for e in evts}
        for eid, e in want.items():
            body = {
                "id": eid,
                "summary": e["title"],
                "description": e["description"],
                "start": {"dateTime": e["start"], "timeZone": e["timezone"]},
                "end": {"dateTime": e["end"], "timeZone": e["timezone"]},
                "extendedProperties": {"private": {MARK: "1", "key": e["key"]}},
            }
            old = existing.get(eid)
            if old is None:
                try:
                    svc.events().insert(calendarId=cal, body=body).execute()
                except Exception:  # id used before by a deleted event: update it instead
                    svc.events().update(calendarId=cal, eventId=eid, body=body).execute()
                created += 1
            elif (
                old.get("summary") != body["summary"]
                or old.get("description") != body["description"]
                or old["start"].get("dateTime", "")[:16] != e["start"][:16]
            ):
                svc.events().update(calendarId=cal, eventId=eid, body=body).execute()
                updated += 1
        for eid in existing.keys() - want.keys():
            svc.events().delete(calendarId=cal, eventId=eid).execute()
            deleted += 1
        return {"calendar": self.calendar_name, "created": created, "updated": updated, "deleted": deleted}
