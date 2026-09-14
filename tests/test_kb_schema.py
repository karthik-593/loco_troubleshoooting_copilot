"""KB schema — the mechanical 'cite the TSD' rule (HANDOFF validate_kb)."""
import copy

import pytest
import yaml
from pydantic import ValidationError

from kb.schema import FAULTS_DIR, Fault, load_fault_file, main, validate_kb


@pytest.fixture(scope="module")
def qlm_dict():
    with open(FAULTS_DIR / "qlm_dropped.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def test_locked_qlm_file_validates():
    f = load_fault_file(FAULTS_DIR / "qlm_dropped.yaml")
    assert f.fault_id == "QLM_dropped"
    assert [s.id for s in f.ordinary_steps] == [
        "check_ht2_compartment", "check_oil_levels", "check_arc_chutes_and_terminals"]
    assert [s.id for s in f.gated_steps] == ["reset_decision"]
    assert f.step("reset_decision").gate.type == "reset_limit"
    assert {r.route_to for r in f.combination_rules} == {"QLM_with_QOP_QRSI", "QLM_with_QLA_QOA"}
    assert "1566969531009-ETTC_TSD.pdf" in f.source_url


def test_whole_kb_validates():
    assert validate_kb() == []
    assert main([]) == 0


def test_missing_file_source_fails(qlm_dict):
    d = copy.deepcopy(qlm_dict); del d["source"]
    with pytest.raises(ValidationError):
        Fault.model_validate(d)


def test_missing_source_url_fails(qlm_dict):
    d = copy.deepcopy(qlm_dict); del d["source_url"]
    with pytest.raises(ValidationError):
        Fault.model_validate(d)


def test_step_without_citation_fails(qlm_dict):
    d = copy.deepcopy(qlm_dict); del d["steps"][0]["source"]
    with pytest.raises(ValidationError, match="no TSD source citation"):
        Fault.model_validate(d)


def test_gate_without_citation_fails(qlm_dict):
    d = copy.deepcopy(qlm_dict); del d["steps"][3]["gate"]["source"]
    with pytest.raises(ValidationError):
        Fault.model_validate(d)


def test_unknown_gate_type_fails(qlm_dict):
    d = copy.deepcopy(qlm_dict); d["steps"][3]["gate"]["type"] = "vibes"
    with pytest.raises(ValidationError):
        Fault.model_validate(d)


def test_unknown_field_fails(qlm_dict):
    d = copy.deepcopy(qlm_dict); d["steps"][0]["llm_hint"] = "just guess"
    with pytest.raises(ValidationError):
        Fault.model_validate(d)


def test_validate_kb_reports_bad_file(tmp_path, qlm_dict):
    d = copy.deepcopy(qlm_dict); del d["source"]
    (tmp_path / "bad.yaml").write_text(yaml.safe_dump(d), encoding="utf-8")
    errs = validate_kb(tmp_path)
    assert len(errs) == 1 and errs[0].startswith("bad.yaml")
    assert main([str(tmp_path)]) == 1
