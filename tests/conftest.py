import pytest

from engine.matcher import load_kb
from engine.state import DiagnosisState, StateUpdate, update_state

QLM = "QLM_dropped"
ORDINARY = ("check_ht2_compartment", "check_oil_levels", "check_arc_chutes_and_terminals")
RESET_STEP = "reset_decision"


@pytest.fixture(scope="session")
def kb():
    return load_kb()


@pytest.fixture(scope="session")
def qlm(kb):
    return kb.get(QLM)


@pytest.fixture
def qlm_state(qlm):
    """A fresh state with QLM matched and its checklist loaded — nothing claimed yet."""
    s = DiagnosisState()
    update_state(s, StateUpdate(fault_id=QLM), qlm)
    return s
