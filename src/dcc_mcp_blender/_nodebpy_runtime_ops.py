"""NodeBPY project entry points; game logic stays in the attached checkout."""

from __future__ import annotations

import importlib
from pathlib import Path
from time import perf_counter

from dcc_mcp_core.skill import skill_error, skill_exception, skill_success

from ._dev_ops import attach_project, enable_addon, get_addon_status, run_entrypoint
from ._scene_files import file_stamp, file_state, guard_before_replacement, observe_external_save


def _project_call(module: str, function: str, args: dict) -> dict:
    started = perf_counter()
    result = run_entrypoint(module, function, args)
    elapsed = perf_counter() - started
    if not result.get("success"):
        result.setdefault("context", {})["project_elapsed_seconds"] = elapsed
        return result
    payload = result.get("context", {}).get("result")
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        return skill_error(
            "NodeBPY project check failed",
            "The project result did not report ok=True.",
            result=result,
            project_elapsed_seconds=elapsed,
        )
    return skill_success("NodeBPY project result", result=payload, project_elapsed_seconds=elapsed)


def _report_save_observation(result: dict, action: str, output: str) -> None:
    """Keep structural verification separate from the adapter's save evidence."""
    try:
        state = file_state()
    except Exception as exc:
        state = {"save_status": "unknown", "observation_error": str(exc)}
    payload = result["context"]["result"]
    details = payload.setdefault("details", {})
    saved = (
        details.get("current_scene_saved") is True
        and state.get("save_status") == "saved"
        and state.get("pending_managed_write") is False
        and bool(state.get("filepath"))
        and Path(state["filepath"]).resolve() == Path(output).expanduser().resolve()
    )
    details["current_scene_saved"] = saved
    details["save_status"] = "saved" if saved else "unknown"
    if not saved:
        payload["status"] = (
            "current_scene_structurally_verified_unsaved"
            if action == "verify"
            else "structurally_verified_save_unconfirmed"
        )
    result["context"]["file_state"] = state


def _check_checkout(project_root: str) -> None:
    root = Path(project_root).expanduser().resolve(strict=True)
    for name in ("nodebpy_cli", "nodebpy_build"):
        module = importlib.import_module(name)
        if Path(module.__file__).resolve().parent != root:
            raise RuntimeError(f"{name} is loaded from another checkout; use a fresh isolated DCC instance")


def prepare(project_root: str, extra_paths: list[str] | None = None, addon_module: str = "mmd_tools") -> dict:
    """Attach explicit dependencies and enable an installed importer in this process."""
    try:
        attached = attach_project(project_root, extra_paths)
        if not attached.get("success") or attached.get("context", {}).get("missing"):
            return skill_error(
                "NodeBPY paths are missing", "Every requested dependency path must exist.", attachment=attached
            )
        _check_checkout(project_root)
        before = get_addon_status(addon_module, operators=["mmd_tools.import_model"])
        if not before.get("success"):
            return before
        enabled_result = enable_addon(addon_module, persist=False, operators=["mmd_tools.import_model"])
        if not enabled_result.get("success"):
            return enabled_result
        doctor = _project_call("nodebpy_cli", "doctor", {"blender": True})
        if not doctor.get("success"):
            return skill_error(
                "NodeBPY dependencies are not ready",
                "Blender doctor failed.",
                addon_before=before,
                activation=enabled_result,
                doctor=doctor,
            )
        return skill_success(
            "NodeBPY dependencies ready", addon_before=before, activation=enabled_result, doctor=doctor
        )
    except Exception as exc:
        return skill_exception(exc, message="Failed to prepare NodeBPY")


def project(project_root: str, action: str, game: str, profile: str, output: str) -> dict:
    """Check inputs, build a new candidate or inspect the live candidate without closing it."""
    try:
        _check_checkout(project_root)
        before = None
        if action == "run":
            refusal = guard_before_replacement()
            if refusal:
                return refusal
            before = file_stamp(output)
        result = _project_call(
            "nodebpy_build",
            "execute",
            {
                "action": action,
                "game": game,
                "profile": profile,
                "output": output,
                "co_work": True,
            },
        )
        if action == "run" and result.get("success"):
            details = result["context"]["result"].get("details", {})
            if details.get("current_scene_saved") is not True:
                return skill_error("NodeBPY save was not verified", "save_unconfirmed", project_result=result)
            status = details.get("scene", {}).get("save_operator_status", [])
            observed = observe_external_save(output, before, status)
            if not observed.get("success"):
                details["current_scene_saved"] = False
                _report_save_observation(result, action, output)
                return skill_error(
                    "NodeBPY save observation is incomplete",
                    "save_unconfirmed",
                    project_result=result,
                    file_state=observed,
                )
            result["context"]["save_observation"] = observed
        if action in {"run", "verify"} and result.get("success"):
            _report_save_observation(result, action, output)
        return result
    except Exception as exc:
        return skill_exception(exc, message="NodeBPY project operation failed")
