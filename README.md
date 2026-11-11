# runcoach

An open-source running coach that plans your training around your life.

- **Your calendar is the app.** Each run is an event in a separate "Training" calendar, for example `Tempo 8 km · 2 x 12 min tempo @ 5:55/km`. The description gives pace in min/km and min/mi, treadmill speed in km/h and mph, the HR target, and why the run changed.
- **Your plan moves when your life changes.** Tell it "trip 8–11 Feb". It shows the plan before and after the change, and nothing changes until you tap Apply.
- **It adapts to your body.** When readiness is low, resting HR is high for 2 days, HRV is low, you slept badly, you did a hard hike, or your legs felt heavy, the run becomes an easy version. It always says why.
- **All your training is in one plan.** Strength sessions are blocks in the plan, and hikes and other sports change the runs after them.
- **Ambient.** Run it as an MCP server, and your AI assistant can do the morning check for you: read your watch and calendar, apply small changes, and ask you about big ones.

> Not medical advice. If you are sick or have pain, do not run.

## How it works

```
watch data, calendar, your words
        │
        ▼
  life changes + body data ──► scheduler (fixed rules) ──► adapt rules ──► Training calendar
                                                                         ├─► web app (Today, Plan, Change, Log)
                                                                         └─► MCP tools for your assistant
```

The AI never edits the plan. It turns what it sees into a **life change** (`away`, `busy`, `treadmill`, `ill`) or **body data**. Then runcoach's own code calculates the new plan, so the result is the same each time and you can test it.

### Scheduler rules

Each week's sessions have a priority: race > long > workout/test > easy. The scheduler places the most important session first:

- No run on a day that a life change blocks. One run each day.
- No hard session next to another hard session (at least 48 h between them).
- No more than 2 run days in a row.
- A session stays in its own week. A session never moves to a day before today. Lost km are never "made up".
- If possible, there is no hard session on the day after a leg-strength session.
- The race never moves.
- If a session does not fit anywhere, it is dropped. Because the most important sessions are placed first, the lowest-priority session is the one that is dropped.

**Small changes** (move a run 1 day, drop an easy run) can be applied by the assistant, which then tells you. **Big changes** (move a key run 2 or more days, drop a long run or workout, anything in race week, illness) need your OK first.

### How the coach adapts (evidence-based, in plain words)

Every automatic change names a rule from the [rule book](src/runcoach/rules.py). Each rule says how sure we are (safety first, experts agree, some studies, or our best guess) and where the idea comes from. See [docs/evidence.md](docs/evidence.md).

1. **You stay in control.** "Keep the original run" overrides any change.
2. **Pain:** if pain changes how you run, or is clear, you rest today. Mild pain gives an easier run. If pain lasts 3 days, the app tells you to see a physio. It never diagnoses.
3. **After illness:** the first 2 runs are easy.
4. **You said you are tired** (or your legs felt heavy, or yesterday was very hard): the run is easier.
5. **Two signs agree** (short sleep compared with your normal, higher resting heart rate for 2 days, signs of stress, low watch readiness, a big hike): the run is easier.
6. **Only one sign:** the coach asks "How do you feel today?" and nothing changes until you answer.

The race is never changed automatically. An easier run = easy pace only, about 25% shorter.

The app talks like a coach, not a lab report. It says "you slept about an hour less than usual", not "5.1 h". The raw numbers are behind "Show the numbers".

## Quick start

```bash
git clone https://github.com/anthonytanyj/runcoach && cd runcoach
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"

export RUNCOACH_PLAN=plans/sample-half-marathon.yaml   # or your own plan
runcoach today
runcoach week 14
runcoach change 2027-02-08 2027-02-11 --note "work trip"          # preview
runcoach change 2027-02-08 2027-02-11 --note "work trip" --apply  # save
runcoach undo
runcoach web            # http://127.0.0.1:8000
```

Your state (life changes, body data, activities, log) is in `~/.runcoach/state.json`. It stays on your computer.

## Web app

`runcoach web` serves a mobile-first web app with these pages:

- **Today:** the run, pace (km and mi), treadmill speed (km/h and mph), time, HR, and why it changed. There is also a "How did today feel?" input.
- **Plan:** all 24 weeks with done, missed, moved, and dropped sessions, and the strength blocks.
- **Change:** enter dates, a type, and a note. You see the plan before and after, then tap Apply.
- **Log:** history, undo, sync buttons, body data, activities, and an `.ics` feed.

The design follows the visual system in [`.claude/skills/landing-page-design`](.claude/skills/landing-page-design/SKILL.md) (from [elayadesign/ai-design-skills](https://github.com/elayadesign/ai-design-skills), MIT): Geist, named text styles, spacing tokens, the nested radius rule, flat backgrounds, Phosphor icons (self-hosted), and one motion curve. Claude Code loads this skill when you work on the UI.

To use it on your phone, run it on a computer that stays on, and reach that computer with [Tailscale](https://tailscale.com). Set `RUNCOACH_PASSWORD` so the pages need a login (user name `runcoach`). You can add the page to your home screen.

## MCP server (ambient coach)

`runcoach mcp` runs an MCP server on stdio. For example, in Claude Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "runcoach": {
      "command": "/path/to/runcoach/.venv/bin/runcoach",
      "args": ["mcp"],
      "env": { "RUNCOACH_PLAN": "/path/to/runcoach/plans/sample-half-marathon.yaml" }
    }
  }
}
```

The server has these tools: `today`, `week`, `check_in`, `log_pain`, `keep_original_run`, `propose_paces`, `accept_paces`, `rule_book`, `preview_change`, `apply_change`, `undo_last_change`, `log_metrics`, `log_activity`, `log_feeling`, `calendar_events`, `sync_google_calendar`, and `sync_garmin`. Its instructions tell the assistant how to do the morning check. To make it ambient, add a daily scheduled task to your assistant at about 06:00. The task text can be: *"Do the runcoach morning check."*

If your assistant already has Garmin and Google Calendar connectors, you do not need to set up anything else here. The assistant reads Garmin, sends the data with `log_metrics` and `log_activity`, and writes the result of `calendar_events` to your calendar.

## Data in

| Source | How |
| --- | --- |
| Your assistant's watch connector | `log_metrics`, `log_activity` (MCP) |
| Garmin, direct | `pip install -e ".[garmin]"`, then set `GARMIN_EMAIL` and `GARMIN_PASSWORD` once, then run `runcoach sync-garmin`. This uses the **unofficial** `garminconnect` library, so it can stop working when Garmin makes changes. It is read only. |
| You | The web app (Change, How did today feel?) |

More sources are planned: FIT file import, Intervals.icu, Apple Health, and Health Connect.

## Calendar out

| Way | How |
| --- | --- |
| Your assistant's calendar connector | `calendar_events` (MCP). Each event has a stable `id` and `key`, so a moved run updates the same event. |
| Google Calendar, direct | `pip install -e ".[google]"`. Create an OAuth "Desktop app" client in Google Cloud and enable the Calendar API. Save the client file as `~/.runcoach/credentials.json`, then run `runcoach sync-google`. It writes only to a calendar named **Training**, and it only changes the events that it made. |
| Any calendar app | `runcoach ics` or the `/calendar.ics` feed. Google refreshes feeds only every 12–24 h. |

## Plan format

A plan is a YAML file. See [`plans/sample-half-marathon.yaml`](plans/sample-half-marathon.yaml) for an example. It contains a race, a start Monday, athlete settings (run times, time zone, HRV baseline), strength blocks, a pace table, and weeks of sessions (`slot`, `type`, `km`, `pace`, `detail`, `strides`). To make your own plan, copy it to `~/.runcoach/plan.yaml` (the default path) and change it. Your plan holds personal data such as heart rate, so keep it out of git: `plans/*.local.yaml` is in `.gitignore` if you prefer to keep it in the repo folder.

## Development

```bash
pytest -q
```

The tests use the sample plan. For example: a work trip from 8 to 11 Feb moves the Wednesday tempo to Friday, drops the Saturday easy run, keeps the Sunday long run, and skips the strength sessions in the trip.

Do not commit tokens or your own health data. `.gitignore` excludes `.runcoach/`, `state.json`, and Google credentials.

## License

AGPL-3.0-or-later. If you host a changed version for other people, you must share your code.
