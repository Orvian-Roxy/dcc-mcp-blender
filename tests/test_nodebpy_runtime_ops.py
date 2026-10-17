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
        result = ops.project(str(tmp_path), "verify", "genshin", "/profile", "/candidate.blend")
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
        assert ops.project(str(tmp_path), "run", "genshin", "/profile", "/candidate.blend") == refusal
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
    with patch.object(
        ops,
        "file_state",
        return_value={"filepath": "/candidate.blend", "save_status": "saved", "pending_managed_write": False},
    ), patch.object(ops, "_check_checkout"), patch.object(
        ops, "guard_before_replacement", return_value=None
    ), patch.object(ops, "file_stamp", return_value=before), patch.object(
        ops, "run_entrypoint", return_value=result
    ), patch.object(ops, "observe_external_save", return_value=observed) as observe:
        response = ops.project(str(tmp_path), "run", "genshin", "/profile", "/candidate.blend")
        assert response["success"] is True
        assert response["context"]["project_elapsed_seconds"] >= 0
        observe.assert_called_once_with("/candidate.blend", before, ["FINISHED"])
        assert response["context"]["save_observation"] == observed
        pending = {"success": False, "context": {"save_status": "pending"}}
        observe.return_value = pending
        response = ops.project(str(tmp_path), "run", "genshin", "/profile", "/candidate.blend")
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
        result = ops.project(str(tmp_path), "verify", "genshin", "/profile", "/candidate.blend")
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
