# Evidence behind the coach

runcoach is a coach, not a doctor. Its rules come from a research summary of
half-marathon and marathon training (November 2026). This page lists what the
coach takes from it. The sources are not yet checked one by one: treat them as
pointers, not proof. Each rule in [`src/runcoach/rules.py`](../src/runcoach/rules.py)
says how sure we are.

## What the coach does because of it

| Idea | What runcoach does | Main sources |
| --- | --- | --- |
| Read several signs together, never one number | One watch sign only asks you a question. A change needs 2 signs that agree, or your own report. | Bourdon et al. 2017 (monitoring consensus); Gabbett et al. 2017 (athlete monitoring cycle) |
| How you feel is part of training load | After each run: effort and how your legs feel. Weekly load = effort × minutes. | Napier et al. 2020 |
| Subjective and device measures often disagree | The runner's answer wins over a single watch number. | Scheltinga et al. 2026 (small study) |
| Pain needs care, not diagnosis | Pain that changes your stride, or is clear, means rest. Pain for 3 days means "see a physio". | Research summary, sections 2E and 6 |
| Do not make up missed sessions | Lost km are never added later. | Research summary, section 2A |
| No load ratio or 10% rule as an injury predictor | Not used to predict injury. Build up is gradual only as a careful default. | Cloosterman et al. 2025; Fokkema et al. 2020 |
| Paces from your own results | A 5K, 10K, or half result updates all paces after you confirm. | Research summary, section 4.4; Riegel 1981 (time formula) |
| No single best intensity mix | The plan keeps a simple mix of easy running, one quality session, and a long run. | Esteve-Lanao et al. 2026 (small trial) |
| Volume and long run go with faster half-marathon times | Used only as a check on a plan, never as a target. Example: the sample plan peaks at 34 km a week. | Fokkema et al. 2020 (observational) |
| Body type is not a plan category | runcoach does not ask for or use weight or body type. | Nikolaidis & Knechtle 2022 |

## What the coach does not claim

- It does not predict injuries.
- It does not diagnose pain or illness.
- Associations from cohort studies are not guarantees for one runner.
