"""Direct access to the optional existing runtime_native MMD allowlist."""

from __future__ import annotations

import importlib
import json
import threading

from dcc_mcp_core.skill import skill_error, skill_success

# These are public mmd_dispatch methods, never Worker actions.
_METHODS = {
    "mmd.inspect": ("mmd_tools", "query"),
    "mmd.toon.capabilities": ("MMD Toon Lookdev", "query"),
    "mmd.toon.inspect": ("MMD Toon Lookdev", "query"),
    "mmd.toon.plan": ("MMD Toon Lookdev", "plan"),
    "mmd.toon.apply": ("MMD Toon Lookdev", "modify"),
    "mmd.toon.validate": ("MMD Toon Lookdev", "validate"),
    "mmd.toon.restore": ("MMD Toon Lookdev", "restore"),
    "mmd.mbts.capabilities": ("MBTs-NG", "query"),
    "mmd.mbts.plan": ("MBTs-NG", "plan"),
    "mmd.mbts.execute": ("MBTs-NG", "modify"),
    "mmd.wiggle.capabilities": ("Wiggle 2", "query"),
    "mmd.wiggle.inspect": ("Wiggle 2", "query"),
    "mmd.wiggle.plan": ("Wiggle 2", "plan"),
    "mmd.wiggle.apply": ("Wiggle 2", "modify"),
    "mmd.wiggle.validate": ("Wiggle 2", "validate"),
    "mmd.wiggle.restore": ("Wiggle 2", "restore"),
}


def list_methods() -> dict:
    """Discover public methods even when the optional provider is unavailable."""
    return skill_success(
        "Native MMD methods; use capabilities to check each installed plugin",
        methods=[{"method": name, "plugin": plugin, "phase": phase} for name, (plugin, phase) in _METHODS.items()],
    )


def call(method: str, parameters: dict | None = None) -> dict:
    """Call the native dispatch unchanged, retaining provider failures and receipts."""
    if threading.current_thread() is not threading.main_thread():
        return skill_error("Wrong thread", "Native MMD calls require the Blender main thread.")
    if not isinstance(method, str) or method not in _METHODS:
        return skill_error("Unsupported method", "Select a public method from list_methods.")
    if parameters is None:
        parameters = {}
    allowed = (
        {"scope", "model_ref"}
        if method == "mmd.inspect"
        else set()
        if method.endswith(".capabilities")
        else {"request"}
    )
    if not isinstance(parameters, dict) or set(parameters) - allowed:
        return skill_error("Invalid parameters", f"Allowed fields: {sorted(allowed)}")
    if allowed == {"request"} and not isinstance(parameters.get("request"), dict):
        return skill_error("Invalid request", "Pass a JSON object in parameters.request.")
    try:
        json.dumps(parameters, allow_nan=False)
    except (TypeError, ValueError):
        return skill_error("Invalid parameters", "Parameters must contain finite JSON values.")
    try:
        dispatch = importlib.import_module("runtime_native.mmd_dispatch")
    except ImportError as exc:
        return skill_error(
            "Native provider unavailable",
            "Use blender-dev.attach_project with the existing blender-mcp-runtime checkout "
            f"and required dependency paths, then retry. Import error: {exc}",
        )
    try:
        # Preserve status/code/data, transaction identifiers, and drift failures exactly.
        return dispatch.execute(method, parameters)
    except Exception as exc:
        return skill_error("Native call failed", f"{type(exc).__name__}: {exc}")
