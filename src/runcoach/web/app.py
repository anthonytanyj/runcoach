"""The runcoach web app: Today, Plan, Change, Log.

Run:  runcoach web --plan plans/sample-half-marathon.yaml   (then open http://localhost:8000)

If you make it reachable from your phone (for example with Tailscale), set
RUNCOACH_PASSWORD so the pages need a login (user name: runcoach).
"""

from __future__ import annotations

import datetime as dt
import os
import secrets
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import calendar_out
from ..engine import Coach
from ..models import ChangeKind, LifeChange, PainReport
from ..rules import RULES
from ..words import feel as feel_words, minutes as minutes_words
from ..paces import km_to_mi
from ..render import description, est_minutes, pace_info, short_title, title

HERE = Path(__file__).parent
_basic = HTTPBasic(auto_error=False)


def _auth(creds: HTTPBasicCredentials | None = Depends(_basic)) -> None:
    pw = os.environ.get("RUNCOACH_PASSWORD")
    if not pw:
        return
    if not creds or not secrets.compare_digest(creds.password.encode(), pw.encode()):
        raise HTTPException(401, headers={"WWW-Authenticate": "Basic"})


def _minutes(time: str) -> float:
    parts = [int(x) for x in time.strip().split(":")]
    if len(parts) == 3:
        return parts[0] * 60 + parts[1] + parts[2] / 60
    if len(parts) == 2:
        return parts[0] + parts[1] / 60
    raise ValueError("Use mm:ss or h:mm:ss")


def create_app(coach_factory) -> FastAPI:
    """``coach_factory`` returns a fresh Coach (so changes on disk are always read)."""
    app = FastAPI(title="runcoach", dependencies=[Depends(_auth)])
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    tpl = Jinja2Templates(directory=HERE / "templates")

    def ctx(request: Request, coach: Coach, page: str, **kw):
        plan = coach.plan
        today = kw.pop("today")
        days_to_race = (plan.race.date - today).days
        return {
            "request": request, "plan": plan, "page": page, "today": today, "days_to_race": days_to_race,
            "title": lambda s: title(plan, s), "short": lambda s: short_title(plan, s), "desc": lambda s: description(plan, s),
            "pace": lambda k: pace_info(plan, k), "mins": lambda s: est_minutes(plan, s),
            "mi": km_to_mi, "q": request.query_params.get("d", ""), "feel": feel_words,
            "minutes_words": minutes_words, "rules": RULES, **kw,
        }

    def _today(d: str | None) -> dt.date:
        return dt.date.fromisoformat(d) if d else dt.date.today()

    @app.get("/", response_class=HTMLResponse)
    def today_page(request: Request, d: str | None = None):
        c = coach_factory()
        t = c.today(_today(d))
        return tpl.TemplateResponse(request, "today.html", ctx(request, c, "today", today=t["date"], t=t))

    @app.get("/plan", response_class=HTMLResponse)
    def plan_page(request: Request, d: str | None = None, km: float | None = None, time: str | None = None):
        c = coach_factory()
        today = _today(d)
        proposal = None
        if km and time:
            try:
                proposal = c.propose_paces(km, _minutes(time)) | {"km": km, "time": time}
            except ValueError:
                proposal = None
        items = c.sessions(today)
        blocks = c.blocks()
        weeks = []
        for w in c.plan.weeks:
            sess = [s for s in items if s.week == w.n]
            done = sum(s.session.km for s in sess if s.status == "done")
            planned = sum(s.session.km for s in sess if not s.dropped)
            rows = [{"kind": "run", "date": s.original_date if s.dropped else s.date, "item": s} for s in sess]
            rows += [{"kind": "lift", "date": b.date, "item": b} for b in blocks if c.plan.week_of(b.date) == w.n]
            rows.sort(key=lambda r: (r["date"], r["kind"] != "run"))
            weeks.append({"w": w, "start": c.plan.week_start(w.n), "rows": rows, "km": planned, "done_km": done,
                          "summary": c.week_summary(w.n, today)})
        current = c.plan.week_of(today)
        return tpl.TemplateResponse(request, "plan.html", ctx(request, c, "plan", today=today, weeks=weeks, current=current, proposal=proposal))

    @app.get("/change", response_class=HTMLResponse)
    def change_page(request: Request, d: str | None = None):
        c = coach_factory()
        today = _today(d)
        return tpl.TemplateResponse(request, "change.html", ctx(request, c, "change", today=today, diff=None, form=None, kinds=list(ChangeKind)))

    @app.post("/change", response_class=HTMLResponse)
    def change_preview(
        request: Request,
        start: str = Form(...), end: str = Form(""), kind: str = Form("away"), note: str = Form(""),
        lifts_ok: bool = Form(False), apply: str = Form(""), d: str = Form(""),
    ):
        c = coach_factory()
        today = _today(d or None)
        try:
            ch = LifeChange(start=dt.date.fromisoformat(start), end=dt.date.fromisoformat(end or start),
                            kind=ChangeKind(kind), note=note.strip(), lifts_ok=lifts_ok)
            if ch.end < ch.start:
                raise ValueError("The end date is before the start date.")
        except ValueError as e:
            return tpl.TemplateResponse(request, "change.html", ctx(request, c, "change", today=today, diff=None,
                                        form=None, kinds=list(ChangeKind), error=str(e)), status_code=400)
        if apply:
            c.apply(ch, today)
            return RedirectResponse(f"/log?applied=1{'&d=' + d if d else ''}", status_code=303)
        diff = c.preview(ch, today)
        form = {"start": start, "end": end or start, "kind": kind, "note": note, "lifts_ok": lifts_ok}
        return tpl.TemplateResponse(request, "change.html", ctx(request, c, "change", today=today, diff=diff,
                                    summary=[tuple(x.split(": ", 1)) for x in diff.summary(c.plan) if ": " in x], form=form, kinds=list(ChangeKind)))

    @app.post("/undo")
    def undo(d: str = Form("")):
        coach_factory().undo()
        return RedirectResponse(f"/log{'?d=' + d if d else ''}", status_code=303)

    @app.post("/feel")
    def feel(day: str = Form(...), effort: str = Form(""), legs: str = Form(""), ready: str = Form(""), d: str = Form("")):
        eff = int(effort) if effort.isdigit() else None
        coach_factory().store.set_feeling(dt.date.fromisoformat(day), eff, legs or None,
                                          ready=ready if ready in ("fine", "tired") else None)
        return RedirectResponse(f"/{'?d=' + d if d else ''}", status_code=303)

    @app.post("/pain")
    def pain(day: str = Form(...), where: str = Form(""), score: str = Form("0"), changed_stride: bool = Form(False), d: str = Form("")):
        sc = max(0, min(10, int(score))) if score.isdigit() else 0
        coach_factory().store.add_pain(PainReport(date=dt.date.fromisoformat(day), where=where.strip(), score=sc,
                                                  changed_stride=changed_stride))
        return RedirectResponse(f"/{'?d=' + d if d else ''}", status_code=303)

    @app.post("/keep")
    def keep(day: str = Form(...), key: str = Form(...), d: str = Form("")):
        coach_factory().store.keep_original(dt.date.fromisoformat(day), key)
        return RedirectResponse(f"/{'?d=' + d if d else ''}", status_code=303)

    @app.post("/paces")
    def paces(km: float = Form(...), time: str = Form(...), d: str = Form("")):
        c = coach_factory()
        c.accept_paces(c.propose_paces(km, _minutes(time)))
        return RedirectResponse(f"/plan{'?d=' + d if d else ''}", status_code=303)

    @app.get("/rules", response_class=HTMLResponse)
    def rules_page(request: Request, d: str | None = None):
        c = coach_factory()
        return tpl.TemplateResponse(request, "rules.html", ctx(request, c, "", today=_today(d), all_rules=list(RULES.values())))

    @app.get("/log", response_class=HTMLResponse)
    def log_page(request: Request, d: str | None = None, applied: int = 0, msg: str = ""):
        c = coach_factory()
        st = c.store.state
        return tpl.TemplateResponse(request, "log.html", ctx(
            request, c, "log", today=_today(d), log=list(reversed(st.log))[:50], changes=st.changes,
            metrics=list(reversed(st.metrics))[:7], activities=list(reversed(st.activities))[:10],
            applied=applied, msg=msg))

    @app.post("/sync/{what}")
    def sync(what: str):
        c = coach_factory()
        try:
            if what == "garmin":
                from ..garmin_in import sync as gsync
                r = gsync(c.store)
                msg = f"Garmin: {r['activities_added']} new activities, body data for {r['metrics_days']} days."
            elif what == "google":
                s = dt.date.today()
                e = s + dt.timedelta(days=27)
                r = calendar_out.GoogleCalendarSync().sync(c, s, e, s)
                msg = " ".join(r["your_moves"]) + (" " if r["your_moves"] else "") + (
                    f"Google Calendar: {r['created']} added, {r['updated']} changed, {r['deleted']} removed.")
            else:
                raise HTTPException(404)
        except HTTPException:
            raise
        except Exception as ex:  # show the problem on the page; do not crash
            msg = f"{what} sync failed: {ex}"
        from urllib.parse import quote
        return RedirectResponse(f"/log?msg={quote(msg)}", status_code=303)

    @app.exception_handler(404)
    async def not_found(request: Request, exc):
        if request.url.path.startswith("/api/"):
            from fastapi.responses import JSONResponse
            return JSONResponse({"detail": "Not found"}, status_code=404)
        c = coach_factory()
        return tpl.TemplateResponse(request, "404.html", ctx(request, c, "", today=dt.date.today()), status_code=404)

    @app.get("/calendar.ics")
    def ics(d: str | None = None):
        c = coach_factory()
        today = _today(d)
        evts = calendar_out.events(c, today - dt.timedelta(days=7), c.plan.race.date, today)
        return Response(calendar_out.to_ics(evts), media_type="text/calendar")

    @app.get("/api/today")
    def api_today(d: str | None = None):
        c = coach_factory()
        t = c.today(_today(d))
        return {
            "date": str(t["date"]), "week": t["week"],
            "runs": [{"title": title(c.plan, r), "description": description(c.plan, r)} for r in t["runs"]],
            "next": [{"date": str(r.date), "title": title(c.plan, r)} for r in t["next"]],
        }

    return app


def app_from_env() -> FastAPI:
    plan = os.environ.get("RUNCOACH_PLAN", str(Path.home() / ".runcoach" / "plan.yaml"))
    state = os.environ.get("RUNCOACH_STATE")
    return create_app(lambda: Coach.from_files(plan, state))
