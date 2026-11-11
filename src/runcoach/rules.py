"""The coach's rule book.

Every automatic decision points to one rule here. Each rule has a plain name
(what the runner sees), how sure we are, and where the idea comes from. This is
what makes the coach checkable: the app always says which rule made a change.

Evidence levels (shown to the runner in plain words):
  safety     - "Safety first": we follow it even without strong proof.
  consensus  - "Coaches and scientists agree": expert consensus or guidelines.
  some       - "Some studies support this": small or observational studies.
  choice     - "Our best guess": a sensible coaching default, not proven.

Sources are listed in docs/evidence.md. They are not yet
checked one by one; treat them as pointers, not proof.

Rules we deliberately do NOT use:
  * "Never add more than 10% per week" as injury prevention.
  * Acute:chronic load ratio as an injury predictor.
  Studies of recreational runners found these do not reliably predict injury
  (Cloosterman 2025; Fokkema 2020). Weekly increases stay gradual as a careful
  default only.
"""

from __future__ import annotations

from dataclasses import dataclass

LEVELS = {
    "safety": "Safety first",
    "consensus": "Coaches and scientists agree",
    "some": "Some studies support this",
    "choice": "Our best guess",
}


@dataclass(frozen=True)
class Rule:
    id: str
    name: str        # short, friendly
    why: str         # one plain sentence the runner sees
    level: str       # key of LEVELS
    source: str      # where the idea comes from

    @property
    def level_text(self) -> str:
        return LEVELS[self.level]


RULES: dict[str, Rule] = {r.id: r for r in [
    Rule("pain_stop", "Pain means rest",
         "Pain that changes how you run, or feels strong, is a reason to stop. Running through it can make it worse.",
         "safety", "Research summary, section 2E and 6: stop or change the session, never diagnose"),
    Rule("pain_mild", "Go gently with mild pain",
         "A little pain can be fine, but keep it easy and stop if it gets worse.",
         "safety", "Research summary, section 4.2: track pain and whether it changes your stride"),
    Rule("pain_see_someone", "Pain that stays needs a check",
         "Pain that lasts several days should be looked at by a physio or doctor.",
         "safety", "Research summary, section 2E: suggest a professional for pain that stays or gets worse"),
    Rule("signs_agree", "Two signs say rest",
         "We never act on one number. When two signs point the same way, we make the run easier.",
         "consensus", "Monitoring consensus (Bourdon 2017) and the athlete monitoring cycle (Gabbett 2017)"),
    Rule("one_sign_ask", "One sign, so we ask you",
         "One number on its own can be wrong, so we ask how you feel before we change anything.",
         "consensus", "Research summary, section 5: signals start a conversation, not a verdict"),
    Rule("you_said_tired", "You know your body",
         "You told us you feel tired or heavy, so the run is easier today.",
         "consensus", "Research summary, sections 4.3 and 5: how you feel is a core part of training load"),
    Rule("illness_return", "Easy return after being sick",
         "After an illness, the first runs are easy so your body can catch up.",
         "choice", "Common coaching practice"),
    Rule("hills_legs", "Big hike counts too",
         "A hike with lots of climbing tires your legs like a run does.",
         "choice", "Research summary, section 4.2: count other exercise in your total training"),
    Rule("you_chose", "Your choice",
         "You chose to keep the run as planned. You stay in control.",
         "consensus", "Research summary, section 6: keep the runner in control"),
    Rule("life_moves", "Your plan moves with your life",
         "A busy day moved this run. Important runs are kept first.",
         "choice", "Coaching practice: keep long runs and workouts, drop easy runs first"),
    Rule("no_makeup", "No catching up",
         "Missed runs are not added later. Adding them makes the next days too hard.",
         "choice", "Research summary, section 2A: do not make up missed sessions"),
    Rule("rest_between_hard", "Rest between hard days",
         "Hard runs need at least a day of rest between them.",
         "choice", "Common coaching practice"),
    Rule("pace_from_test", "Paces from your own results",
         "Your paces come from your own test or race, not from a formula about people in general.",
         "some", "Research summary, section 4.4; race time formula (Riegel 1981)"),
    Rule("effort_load", "Effort times minutes",
         "We count how hard each run felt and how long it was. This shows how tiring a week really was.",
         "some", "Session effort method in runners (Napier 2020)"),
]}


def get(rule_id: str) -> Rule:
    return RULES[rule_id]
