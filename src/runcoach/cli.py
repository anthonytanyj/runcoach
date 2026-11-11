"""Command line: runcoach today | week | change | undo | ics | web | mcp | sync-garmin | sync-google."""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from pathlib import Path

from . import calendar_out
from .engine import Coach
from .models import ChangeKind, LifeChange
from .render import description, one_line


def _coach(args) -> Coach:
    return Coach.from_files(args.plan, args.state)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="runcoach", description="An open-source, ambient running coach.")
    p.add_argument("--plan", default=os.environ.get("RUNCOACH_PLAN", str(Path.home() / ".runcoach" / "plan.yaml")))
    p.add_argument("--state", default=os.environ.get("RUNCOACH_STATE"))
    p.add_argument("--date", help="act as if today is YYYY-MM-DD")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("today", help="today's run")
    w = sub.add_parser("week", help="one plan week")
    w.add_argument("n", type=int, nargs="?")
    ch = sub.add_parser("change", help="preview (or --apply) a life change")
    ch.add_argument("start")
    ch.add_argument("end", nargs="?")
    ch.add_argument("--kind", default="away", choices=[k.value for k in ChangeKind])
    ch.add_argument("--note", default="")
    ch.add_argument("--lifts-ok", action="store_true")
    ch.add_argument("--apply", action="store_true")
    sub.add_parser("undo", help="undo the last life change")
    ics = sub.add_parser("ics", help="write an .ics file")
    ics.add_argument("out", nargs="?", default="training.ics")
    web = sub.add_parser("web", help="run the web app")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=8000)
    sub.add_parser("mcp", help="run the MCP server (stdio)")
    sg = sub.add_parser("sync-garmin", help="read recent data from Garmin")
    sg.add_argument("--days", type=int, default=14)
    sc = sub.add_parser("sync-google", help="write the Training calendar in Google")
    sc.add_argument("--days", type=int, default=28)

    args = p.parse_args(argv)
    os.environ["RUNCOACH_PLAN"] = args.plan
    if args.state:
        os.environ["RUNCOACH_STATE"] = args.state
    today = dt.date.fromisoformat(args.date) if args.date else dt.date.today()

    if args.cmd == "web":
        import uvicorn

        from .web.app import app_from_env

        uvicorn.run(app_from_env(), host=args.host, port=args.port)
        return 0
    if args.cmd == "mcp":
        from .mcp_server import main as mcp_main

        mcp_main()
        return 0

    c = _coach(args)
    if args.cmd == "today":
        t = c.today(today)
        print(f"{today:%a %d %b %Y} - week {t['week']}")
        if not t["runs"]:
            print("Rest day." + "".join(f" {b.block.name}." for b in t["lifts"]))
        for r in t["runs"]:
            print(one_line(c.plan, r))
            print(description(c.plan, r))
        print("\nNext:")
        for r in t["next"]:
            print("  " + one_line(c.plan, r))
    elif args.cmd == "week":
        n = args.n or c.plan.week_of(today) or 1
        for s in c.week(n, today):
            print(one_line(c.plan, s) + (f"  [{s.status}]" if s.status != "planned" else ""))
            for a in s.adjustments:
                print(f"    - {a.reason}")
    elif args.cmd == "change":
        change = LifeChange(
            start=dt.date.fromisoformat(args.start),
            end=dt.date.fromisoformat(args.end or args.start),
            kind=ChangeKind(args.kind), note=args.note, lifts_ok=args.lifts_ok,
        )
        diff = c.apply(change, today) if args.apply else c.preview(change, today)
        for line in diff.summary(c.plan):
            print(line)
        print("Applied." if args.apply else ("Big change: check it, then add --apply." if diff.needs_ok else "Add --apply to save."))
    elif args.cmd == "undo":
        e = c.undo()
        print(f"Undid: {e.summary}" if e else "Nothing to undo.")
    elif args.cmd == "ics":
        Path(args.out).write_text(calendar_out.to_ics(calendar_out.events(c, today, None, today)), encoding="utf-8")
        print(f"Wrote {args.out}")
    elif args.cmd == "sync-garmin":
        from .garmin_in import sync

        print(sync(c.store, args.days, today))
    elif args.cmd == "sync-google":
        e = today + dt.timedelta(days=args.days - 1)
        print(calendar_out.GoogleCalendarSync().sync(calendar_out.events(c, today, e, today), today, e))
    return 0


if __name__ == "__main__":
    sys.exit(main())
