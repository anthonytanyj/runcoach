from pathlib import Path

import pytest

from runcoach.engine import Coach
from runcoach.models import Plan
from runcoach.store import Store

PLAN = Path(__file__).resolve().parents[1] / "plans" / "sample-half-marathon.yaml"


@pytest.fixture
def plan() -> Plan:
    return Plan.load(PLAN)


@pytest.fixture
def coach(plan, tmp_path) -> Coach:
    return Coach(plan, Store(tmp_path / "state.json"))
