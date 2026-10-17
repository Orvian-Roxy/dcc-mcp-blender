from unittest.mock import patch

import pytest

from dcc_mcp_blender import _nodebpy_runtime_ops as ops


def test_missing_attached_paths_do_not_report_ready():
    with patch.object(
        ops, "attach_project", return_value={"success": True, "context": {"missing": ["/missing"]}}
    ), patch.object(ops, "run_entrypoint") as run:
        assert ops.prepare("/missing")["success"] is False
        run.assert_not_called()


def test_nested_doctor_failure_is_not_transport_success():
    with patch.object(
        ops,
        "run_entrypoint",
        return_value={
            "success": True,
            "context": {"result": {"ok": False, "checks": [{"name": "shader_runtime", "ok": False}]}},
        },
    ):
        result = ops._project_call("nodebpy_cli", "doctor", {"blender": True})
        assert result["success"] is False
        assert result["context"]["project_elapsed_seconds"] >= 0


def test_prepare_reports_transient_activation_and_actual_doctor(tmp_path):
    state = {"success": True, "context": {"enabled_runtime": False, "on_disk": True}}
    activation = {"success": True, "context": {"preferences_saved": False}}
    with patch.object(ops, "attach_project", return_value={"success": True, "context": {"missing": []}}), patch.object(
        ops, "_check_checkout"
    ), patch.object(ops, "get_addon_status", return_value=state), patch.object(
        ops, "enable_addon", return_value=activation
    ) as enable, patch.object(
        ops, "run_entrypoint", return_value={"success": True, "context": {"result": {"ok": True, "checks": []}}}
    ):
        result = ops.prepare(str(tmp_path), addon_module="bl_ext.local.mmd_tools")
        assert result["success"] is True
        enable.assert_called_once_with("bl_ext.local.mmd_tools", persist=False, operators=["mmd_tools.import_model"])
        assert result["context"]["addon_before"] == state
        assert result["context"]["activation"] == activation


def test_project_uses_function_entry_not_quitting_cli(tmp_path):
    with patch.object(ops, "_check_checkout"), patch.object(
        ops, "file_state", return_value={"save_status": "unknown"}
    ), patch.object(
        ops, "guard_before_replacement", side_effect=AssertionError("verify added replacement guard")
    ), patch.object(
        ops,
        "run_entrypoint",
        return_value={
            "success": True,
            "context": {"result": {"ok": True, "status": "current_scene_structurally_verified_unsaved"}},
        },
    ) as entry:
        result = ops.project(str(tmp_path), "verify", "genshin", "/profile", "/candidate.blend", full=True)
        assert result["success"] is True
        entry.assert_called_once_with(
            "nodebpy_build",
            "execute",
            {
                "action": "verify",
                "game": "genshin",
                "profile": "/profile",
                "output": "/candidate.blend",
                "co_work": True,
            },
        )


def test_run_refuses_pending_input_before_calling_project(tmp_path):
    refusal = {"success": False, "error": "unsaved_changes"}
    with patch.object(ops, "_check_checkout"), patch.object(
        ops, "guard_before_replacement", return_value=refusal
    ), patch.object(ops, "run_entrypoint") as run:
        assert ops.project(str(tmp_path), "run", "genshin", "/profile", "/candidate.blend", full=True) == refusal
        run.assert_not_called()


def test_verified_build_observes_external_save_without_saving_again(tmp_path):
    result = {
        "success": True,
        "context": {
            "result": {
                "ok": True,
                "details": {"current_scene_saved": True, "scene": {"save_operator_status": ["FINISHED"]}},
            }
        },
    }
    observed = {"success": True, "context": {"save_status": "saved"}}
    before = {"size": 100, "mtime_ns": 1}
    with patch.object(ops, "_retain_build_report", return_value=True), patch.object(
        ops,
        "file_state",
        return_value={"filepath": "/candidate.blend", "save_status": "saved", "pending_managed_write": False},
    ), patch.object(ops, "_check_checkout"), patch.object(
        ops, "guard_before_replacement", return_value=None
    ), patch.object(ops, "file_stamp", return_value=before), patch.object(
        ops, "run_entrypoint", return_value=result
    ), patch.object(ops, "observe_external_save", return_value=observed) as observe:
        response = ops.project(str(tmp_path), "run", "genshin", "/profile", "/candidate.blend", full=True)
        assert response["success"] is True
        assert response["context"]["project_elapsed_seconds"] >= 0
        observe.assert_called_once_with("/candidate.blend", before, ["FINISHED"])
        assert response["context"]["save_observation"] == observed
        pending = {"success": False, "context": {"save_status": "pending"}}
        observe.return_value = pending
        response = ops.project(str(tmp_path), "run", "genshin", "/profile", "/candidate.blend", full=True)
        assert response["success"] is False
        assert response["context"]["file_state"] == pending


@pytest.mark.parametrize("pending,status", [(True, "unknown"), (False, "unknown"), (True, "saved")])
def test_verify_downgrades_project_save_claim_when_managed_python_write_is_pending(tmp_path, pending, status):
    payload = {
        "ok": True,
        "status": "current_saved_scene_verified",
        "human_approved": False,
        "details": {"current_scene_saved": True, "scene": {"manual_edit": "present"}},
    }
    state = {"filepath": "/candidate.blend", "save_status": status, "pending_managed_write": pending}
    with patch.object(ops, "_check_checkout"), patch.object(ops, "file_state", return_value=state), patch.object(
        ops, "run_entrypoint", return_value={"success": True, "context": {"result": payload}}
    ):
        result = ops.project(str(tmp_path), "verify", "genshin", "/profile", "/candidate.blend", full=True)
        assert result["success"] is True
        actual = result["context"]["result"]
        assert actual["status"] == "current_scene_structurally_verified_unsaved"
        assert actual["details"]["current_scene_saved"] is False
        assert actual["details"]["scene"] == {"manual_edit": "present"}
        assert actual["human_approved"] is False


def test_failed_entrypoint_keeps_elapsed_time():
    with patch.object(
        ops, "run_entrypoint", return_value={"success": False, "context": {"error": "failed"}}
    ), patch.object(ops, "perf_counter", side_effect=[10.0, 10.25]):
        result = ops._project_call("nodebpy_cli", "doctor", {"blender": True})
        assert result["success"] is False
        assert result["context"]["project_elapsed_seconds"] == 0.25


def test_default_success_is_bounded_and_full_is_explicit(tmp_path):
    payload = {
        "ok": True,
        "game": "WutheringWaves",
        "status": "inputs_checked",
        "output": "/candidate.blend",
        "human_approved": False,
        "details": {
            "checked_sources": ["x" * 1000] * 100,
            "slots": 52,
            "output_kind": "diagnostic",
            "contract_limits": ["unsupported shader effect"],
            "pending_visual_gates": ["reference comparison"],
            "acceptance": {
                "status": "diagnostic_only",
                "candidate": False,
                "checkpoint": False,
                "pending": ["visual review"],
            },
        },
    }
    with patch.object(ops, "_check_checkout"), patch.object(
        ops, "run_entrypoint", return_value={"success": True, "context": {"result": payload}}
    ):
        result = ops.project(str(tmp_path), "check", "wuwa", "/profile", "/candidate.blend")
        assert "details" not in result["context"]["result"]
        assert result["context"]["result"]["metrics"]["slots"] == 52
        assert result["context"]["result"]["output_kind"] == "diagnostic"
        assert result["context"]["result"]["contract_limits"] == ["unsupported shader effect"]
        assert result["context"]["result"]["acceptance"]["status"] == "diagnostic_only"
        assert len(str(result)) < 2000
        detailed = ops.project(str(tmp_path), "check", "wuwa", "/profile", "/candidate.blend", full=True)
        assert detailed["context"]["result"]["details"] == payload["details"]


def test_default_failure_keeps_complete_project_evidence(tmp_path):
    evidence = "failed-input:" + "x" * 10000
    with patch.object(ops, "_check_checkout"), patch.object(
        ops, "run_entrypoint", return_value={"success": True, "context": {"result": {"ok": False, "details": evidence}}}
    ):
        result = ops.project(str(tmp_path), "check", "wuwa", "/profile", "/candidate.blend")
        assert result["success"] is False
        assert result["context"]["result"]["context"]["result"]["details"] == evidence


@pytest.mark.parametrize("existing_report", [False, True])
@pytest.mark.parametrize("full", [False, True])
def test_build_receipt_is_retained_once_and_verify_does_not_replace_it(tmp_path, existing_report, full):
    import json

    output = tmp_path / "candidate.blend"
    report = output.with_suffix(".dcc-build-report.json")
    if existing_report:
        report.write_text("previous report")
    payload = {
        "ok": True,
        "game": "ZenlessZoneZero",
        "output": str(output),
        "status": "structurally_verified_pending_visual",
        "details": {
            "current_scene_saved": True,
            "scene": {
                "save_operator_status": ["FINISHED"],
                "import": {"source_faces": 33},
                "timings_seconds": {"total": 1.25},
                "preflight": {"output_kind": "diagnostic"},
                "first": {"slots": 17},
            },
        },
    }
    state = {"filepath": str(output), "save_status": "saved", "pending_managed_write": False}
    with patch.object(ops, "_check_checkout"), patch.object(
        ops, "guard_before_replacement", return_value=None
    ), patch.object(ops, "file_stamp", return_value=None), patch.object(
        ops, "file_state", return_value=state
    ), patch.object(ops, "observe_external_save", return_value={"success": True}), patch.object(
        ops, "run_entrypoint", return_value={"success": True, "context": {"result": payload}}
    ) as entry:
        result = ops.project(str(tmp_path), "run", "zzz", "/profile", str(output), full=full)
        assert result["success"] is True
        if existing_report:
            assert result["context"]["report_error"]
            assert result["context"]["warning"]
            assert result["context"]["result"]["details"]["scene"]["import"] == {"source_faces": 33}
            assert report.read_text() == "previous report"
        else:
            assert result["context"]["report_path"] == str(report)
            if not full:
                assert result["context"]["result"]["output_kind"] == "diagnostic"
            receipt = json.loads(report.read_text())
            assert receipt["context"]["result"]["details"]["scene"]["import"] == {"source_faces": 33}
            assert receipt["context"]["result"]["details"]["scene"]["timings_seconds"] == {"total": 1.25}
            before = report.read_bytes()
            result = ops.project(str(tmp_path), "verify", "zzz", "/profile", str(output))
            assert result["context"]["build_report_path"] == str(report)
            assert report.read_bytes() == before
            assert entry.call_count == 2


def test_verify_report_discovery_failure_preserves_structural_result(tmp_path):
    payload = {
        "ok": True,
        "status": "current_scene_structurally_verified_unsaved",
        "details": {"current_scene_saved": False, "scene": {"slots": 17}},
    }
    with patch.object(ops, "_check_checkout"), patch.object(
        ops, "run_entrypoint", return_value={"success": True, "context": {"result": payload}}
    ), patch.object(ops, "file_state", return_value={"save_status": "unknown"}), patch.object(
        ops.Path, "is_file", side_effect=PermissionError("report inaccessible")
    ):
        result = ops.project(str(tmp_path), "verify", "zzz", "/profile", "/candidate.blend")
        assert result["success"] is True
        assert "PermissionError" in result["context"]["report_error"]
        assert result["context"]["result"]["details"]["scene"] == {"slots": 17}
