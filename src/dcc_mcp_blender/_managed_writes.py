"""Protect unsaved managed writes when Blender's dirty flag has not changed."""

from __future__ import annotations

import sys
import threading
from contextvars import ContextVar
from functools import wraps

_write_revision = 0
_saved_revision = 0
_call_prior_revision = ContextVar("blender_call_prior_write_revision", default=None)
_generic_write_active = ContextVar("blender_generic_write_active", default=False)
_FILE_BOUNDARIES = {
    ("blender-scene", "open_scene"),
    ("blender-scene", "new_scene"),
    ("blender-scene", "close_scene"),
    ("blender-scene", "save_scene"),
    ("blender-geometry", "save_blend"),
    ("blender-nodebpy-runtime", "project"),
}


def pending_write() -> bool:
    prior = _call_prior_revision.get()
    return _generic_write_active.get() or _saved_revision < (_write_revision if prior is None else prior)


def current_revision() -> int:
    return _write_revision


def can_confirm_save() -> bool:
    return not _generic_write_active.get()


def saved_through(revision: int) -> None:
    global _saved_revision
    if can_confirm_save():
        _saved_revision = max(_saved_revision, revision)


def install_write_guard(bridge, get_skill) -> None:
    """Wrap this adapter bridge once, including its host-thread fast path."""
    if getattr(bridge, "_blender_write_guard_installed", False):
        return
    original = bridge._dispatch_raw

    def dispatch(func, args, kwargs, context):
        @wraps(func)
        def invoke(*call_args, **call_kwargs):
            global _write_revision
            if threading.current_thread() is not threading.main_thread():
                return func(*call_args, **call_kwargs)
            tool_name = context.action_name.rsplit("__", 1)[-1]
            params = args[1] if len(args) > 1 and isinstance(args[1], dict) else kwargs
            if (context.skill_name, tool_name) == ("blender-nodebpy-runtime", "project"):
                # Older GI checkouts still switch bindings/reload images. The claim
                # belongs to the loaded execute function and cannot survive its replacement.
                execute = getattr(sys.modules.get("nodebpy_build"), "execute", None)
                observational_gi = params.get("game") in ("genshin", "Genshin") and "Genshin" in getattr(
                    execute, "read_only_verify_games", ()
                )
                if params.get("action") == "check" or (
                    params.get("action") == "verify"
                    and (observational_gi or params.get("game") in ("wuwa", "WutheringWaves", "hsr", "HonkaiStarRail"))
                ):
                    return func(*call_args, **call_kwargs)
            try:
                skill = get_skill(context.skill_name) if context.skill_name else None
                tools = getattr(skill, "tools", ()) if skill is not None else ()
                action = context.action_name
                declared = next(
                    (tool for tool in tools if action == tool.name or action.endswith("__" + tool.name)), None
                )
            except Exception:
                # Missing discovery metadata cannot make a possible write read-only.
                declared = None
            # Capture restores its render settings; navigation/display only change viewport UI.
            # Neither creates a scene edit, and neither clears preexisting writes.
            if declared is not None and (
                declared.read_only
                or (context.skill_name, tool_name)
                in {
                    ("blender-render", "capture_viewport"),
                    ("blender-scene", "set_viewport_view"),
                }
            ):
                return func(*call_args, **call_kwargs)
            if declared is not None and (context.skill_name, tool_name) == ("blender-node-runtime", "preview_variants"):
                # Protect transient edits while running; only a complete restoration
                # receipt avoids adding a lasting write. Nested writes keep their revisions.
                generic_token = _generic_write_active.set(True)
                restored = False
                try:
                    result = func(*call_args, **call_kwargs)
                    receipt = result.get("context") if isinstance(result, dict) else None
                    restored = (
                        isinstance(receipt, dict)
                        and receipt.get("restored") is True
                        and receipt.get("mutated") is False
                    )
                    return result
                finally:
                    if not restored:
                        _write_revision += 1
                    _generic_write_active.reset(generic_token)
            # ponytail: process-only tools are conservatively writes; refine metadata after measured save overhead.
            prior = _write_revision
            _write_revision += 1
            boundary = (context.skill_name, tool_name) in _FILE_BOUNDARIES
            if boundary and context.skill_name == "blender-nodebpy-runtime":
                boundary = params.get("action") == "run"
            # Only audited file/project actions may discount their own conservative mark.
            token = _call_prior_revision.set(prior if boundary else None)
            generic_token = _generic_write_active.set(_generic_write_active.get() or not boundary)
            try:
                return func(*call_args, **call_kwargs)
            finally:
                _generic_write_active.reset(generic_token)
                _call_prior_revision.reset(token)

        return original(invoke, args, kwargs, context)

    bridge._dispatch_raw = dispatch
    bridge._blender_write_guard_installed = True
