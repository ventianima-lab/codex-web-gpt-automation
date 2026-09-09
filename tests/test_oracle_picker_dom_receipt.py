"""Run-local DOM proof receipts: no browser or provider invocation."""
import copy
import hashlib
import json

import pytest
from test_chatgpt_oracle_state import load_state, manifest


def observed_proof():
    return {
        "schema": "codex.oracle.picker-dom-proof/v1",
        "latestClicked": True, "stableReads": 2,
        "modelRows": [
            {"text": "Latest", "role": "menuitemradio", "checked": "true", "visible": True},
            {"text": "5.6", "role": "menuitemradio", "checked": "false", "visible": True},
        ],
        "composer": {"text": "Thinking effort", "ariaLabel": None, "visible": True},
        "modelSignals": [{"text": "6Pro", "role": "menuitem", "expanded": "false", "visible": True}],
        "slider": {"minimum": 0, "maximum": 4, "current": 4, "ordinal": 5, "total": 5,
                   "displayOrdinal": 5, "displayTotal": 5, "atMaximum": True,
                   "text": "Pro, 5 of 5.Use Left and Right arrow keys to adjust power.", "visible": True},
    }


def run_layout(tmp_path):
    state = load_state()
    mission = tmp_path / "mission.md"
    mission.write_text("work", encoding="utf-8")
    config = state.load_manifest(manifest(
        tmp_path, mission.resolve(), model="gpt-5.6-sol", model_strategy="current",
        thinking_time="pro", browser_intent=state.current_browser_intent("pro"),
    ))
    layout = state.create_layout(config)
    state.write_json_atomic(layout.state_path, state.state_payload(config, layout, status="running", resolved_version="0.18.0"))
    return state, layout


def emit(state, layout, proof):
    line = state.PICKER_DOM_LOG_PREFIX + json.dumps(proof, ensure_ascii=False)
    layout.stdout_path.write_text(line + "\n", encoding="utf-8")
    return line


def test_structured_receipt_binds_raw_dom_and_exact_run_log(tmp_path):
    state, layout = run_layout(tmp_path)
    proof = observed_proof()
    line = emit(state, layout, proof)
    receipt = state.capture_picker_profile_receipt(layout.state_path)
    assert receipt["payload"]["schema"].endswith("/v2")
    observed = receipt["payload"]["observed"]
    assert observed["dom_proof"] == proof
    assert observed["log_line_sha256"] == hashlib.sha256(line.encode()).hexdigest()
    assert receipt["payload"]["stdout_sha256"] == hashlib.sha256(layout.stdout_path.read_bytes()).hexdigest()
    assert state.proven_picker_profile_receipt(layout.state_path) == receipt
    layout.stdout_path.write_text(line + "\nchanged\n", encoding="utf-8")
    assert state.proven_picker_profile_receipt(layout.state_path) is None


@pytest.mark.parametrize("section,key,value", [
    ("modelRows", "checked", "false"),
    ("modelRows", "text", "5.6"),
    ("modelRows", "visible", False),
    ("composer", "text", "5.6 Pro"),
    ("composer", "visible", False),
    ("modelSignals", "text", "5.6Pro"),
    ("modelSignals", "visible", False),
    ("slider", "current", 3),
    ("slider", "displayOrdinal", 4),
    ("slider", "total", 6),
    ("slider", "text", "Extra High, 4 of 5"),
    ("slider", "visible", False),
    ("slider", "ordinal", True),
])
def test_mismatched_observation_cannot_be_sealed(tmp_path, section, key, value):
    state, layout = run_layout(tmp_path)
    proof = observed_proof()
    target = proof[section][0] if isinstance(proof[section], list) else proof[section]
    target[key] = value
    emit(state, layout, proof)
    assert state.capture_picker_profile_receipt(layout.state_path) is None
    assert not state.picker_profile_receipt_path(layout.state_path.parent).exists()


@pytest.mark.parametrize("change", ["unclicked", "unstable", "doublechecked", "duplicate", "human", "malformed"])
def test_ambiguous_or_log_only_proof_is_rejected(tmp_path, change):
    state, layout = run_layout(tmp_path)
    proof = observed_proof()
    if change == "unclicked":
        proof["latestClicked"] = False
    elif change == "unstable":
        proof["stableReads"] = 1
    elif change == "doublechecked":
        proof["modelRows"][1]["checked"] = "true"
    line = emit(state, layout, proof)
    if change == "duplicate":
        layout.stdout_path.write_text(line + "\n" + line + "\n", encoding="utf-8")
    elif change == "human":
        layout.stdout_path.write_text("[browser] Thinking time: Latest / 6 Pro (Latest explicitly selected)\n", encoding="utf-8")
    elif change == "malformed":
        layout.stdout_path.write_text(state.PICKER_DOM_LOG_PREFIX + '{"schema":"bad","schema":"duplicate"}\n', encoding="utf-8")
    assert state.capture_picker_profile_receipt(layout.state_path) is None


def test_receipt_dom_tamper_is_rejected_even_with_rehashed_receipt(tmp_path):
    state, layout = run_layout(tmp_path)
    emit(state, layout, observed_proof())
    receipt = state.capture_picker_profile_receipt(layout.state_path)
    payload = copy.deepcopy(receipt["payload"])
    payload["observed"]["dom_proof"]["composer"]["text"] = "invented"
    path = state.picker_profile_receipt_path(layout.state_path.parent)
    state.write_json_atomic(path, payload)
    run = state.load_state(layout.state_path)
    run["picker_profile"]["receipt_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    state.write_json_atomic(layout.state_path, run)
    assert state.proven_picker_profile_receipt(layout.state_path) is None


def test_exact_legacy_receipt_remains_recovery_only(tmp_path):
    state, layout = run_layout(tmp_path)
    run = state.load_state(layout.state_path)
    intent = run["picker_profile"]["requested"]
    line = state._picker_profile_log_line(intent)
    layout.stdout_path.write_text(line + "\n", encoding="utf-8")
    path = state.picker_profile_receipt_path(layout.state_path.parent)
    legacy = {
        "schema": state.PICKER_PROFILE_RECEIPT_SCHEMA, "verified": True,
        "run_id": run["run_id"], "slug": run["oracle"]["slug"],
        "mission_sha256": run["mission"]["sha256"],
        "project_root_sha256": run["ownership"]["project_root_sha256"],
        "source_thread_id": state.source_thread_id_from_state(run),
        "requested": intent, "stdout_path": str(layout.stdout_path),
        "stdout_sha256": hashlib.sha256(layout.stdout_path.read_bytes()).hexdigest(),
        "observed": {"model_row": "Latest", "model_row_checked": True,
                     "thinking_time": "pro", "slider_ordinal": 5, "slider_total": 5,
                     "displayed_effort": "6 Pro", "effort_checked": True, "log_line": line},
    }
    state.write_json_atomic(path, legacy)
    run["picker_profile"].update(verified=True, receipt_path=str(path),
                                 receipt_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    state.write_json_atomic(layout.state_path, run)
    assert state.proven_picker_profile_receipt(layout.state_path) is None
    run["picker_profile"].pop("proof_schema")
    state.write_json_atomic(layout.state_path, run)
    assert state.proven_picker_profile_receipt(layout.state_path) is not None
