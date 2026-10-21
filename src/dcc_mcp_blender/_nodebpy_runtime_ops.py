"""NodeBPY project entry points; game logic stays in the attached checkout."""

from __future__ import annotations

import importlib
import json
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


def _summarize_success(result: dict) -> dict:
    """Bound successful project output; failures retain the complete project result."""
    payload = result["context"]["result"]
    details = payload.get("details", {})
    scene = details.get("scene", details)
    scale = scene.get("first", scene)
    metrics = {
        key: scale[key]
        for key in (
            "slots",
            "faces",
            "vertices",
            "surface_slots",
            "outline_slots",
            "materials",
            "rigid_bodies",
            "joints",
            "parameters_checked",
            "resource_bindings_checked",
        )
        if isinstance(scale.get(key), (int, float))
    }
    if isinstance(scale.get("components"), dict):
        metrics["components"] = {
            key: value for key, value in list(scale["components"].items())[:3] if isinstance(value, int)
        }
    if isinstance(scale.get("checked_sources"), list):
        metrics["checked_sources"] = len(scale["checked_sources"])
    summary = {
        key: payload[key]
        for key in ("ok", "status", "game", "output", "human_approved", "output_kind")
        if key in payload
    }
    summary.update(
        current_scene_saved=details.get("current_scene_saved"),
        save_status=details.get("save_status", "not_checked"),
        metrics=metrics,
    )
    preflight = scene.get("preflight", {})
    output_kind = payload.get("output_kind", scale.get("output_kind", preflight.get("output_kind")))
    if output_kind is not None:
        summary["output_kind"] = output_kind
    for key in ("contract_limits", "pending_visual_gates", "manual_visual_review"):
        values = scale.get(key, preflight.get(key, []))
        if values:
            summary[key + "_count"] = len(values)
            summary[key] = [str(item)[:512] for item in values[:5]]
    acceptance = scene.get("acceptance", {})
    if acceptance:
        summary["acceptance"] = {
            key: acceptance[key] for key in ("status", "candidate", "checkpoint") if key in acceptance
        }
        pending = acceptance.get("pending", [])
        summary["acceptance"]["pending_count"] = len(pending)
        summary["acceptance"]["pending"] = [str(item)[:512] for item in pending[:5]]
    warnings = scale.get("warnings", [])
    if warnings:
        summary["warning_count"] = len(warnings)
        summary["warnings"] = [str(item)[:512] for item in warnings[:3]]
    context = {"result": summary, "project_elapsed_seconds": result["context"]["project_elapsed_seconds"]}
    for key in ("report_path", "build_report_path"):
        if key in result["context"]:
            context[key] = result["context"][key]
    return skill_success("NodeBPY project result", **context)


def _retain_build_report(result: dict, output: str) -> bool:
    """Keep this build's full receipt without replacing an older report."""
    try:
        path = Path(output).expanduser().resolve().with_suffix(".dcc-build-report.json")
        with path.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
    except Exception as exc:
        result["context"]["report_error"] = f"{type(exc).__name__}: {exc}"
        result["context"]["warning"] = (
            "Build succeeded; full evidence is returned because its report could not be written."
        )
        return False
    result["context"]["report_path"] = str(path)
    return True


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
        state = before.get("context", {})
        if state.get("enabled_runtime") and state.get("operators", {}).get("mmd_tools.import_model"):
            enabled_result = before
        else:
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


def project(project_root: str, action: str, game: str, profile: str, output: str, full: bool = False) -> dict:
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
            if action == "run" and not _retain_build_report(result, output):
                return result
            if action == "verify":
                try:
                    report = Path(output).expanduser().resolve().with_suffix(".dcc-build-report.json")
                    if report.is_file():
                        result["context"]["build_report_path"] = str(report)
                except Exception as exc:
                    result["context"]["report_error"] = f"{type(exc).__name__}: {exc}"
                    return result
        return _summarize_success(result) if result.get("success") and not full else result
    except Exception as exc:
        return skill_exception(exc, message="NodeBPY project operation failed")
