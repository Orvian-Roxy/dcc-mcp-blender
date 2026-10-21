"""Main-thread file lifecycle operations shared by typed tools and project code.

Trusted Python can call bpy directly; this module is not a Python sandbox.
"""

from __future__ import annotations

import logging
from pathlib import Path

from dcc_mcp_core.skill import skill_error, skill_exception, skill_success

from ._managed_writes import can_confirm_save, current_revision, pending_write, saved_through
from ._script_guard import guard_main_thread

# One process-local observation, not a task history or a replay queue.
_LAST_SAVE = {}
_LAST_OPEN = {}
logger = logging.getLogger(__name__)


def _replacement_guard(bpy):
    images = [
        image.name for image in bpy.data.images if image.is_dirty and image.type not in {"RENDER_RESULT", "COMPOSITING"}
    ]
    if bpy.data.is_dirty or images or pending_write():
        return skill_error(
            "Current edits must be saved before replacing or closing the scene",
            "unsaved_changes",
            filepath=bpy.data.filepath,
            scene_dirty=bool(bpy.data.is_dirty),
            pending_managed_write=pending_write(),
            dirty_images=images,
            prompt="Save the scene and edited image pixels explicitly, then retry this operation.",
        )
    return None


def open_scene(filepath: str) -> dict:
    """Open a scene without discarding edits or replacing the user's layout."""
    refusal = guard_main_thread("open_scene")
    if refusal:
        return refusal
    try:
        import bpy

        refusal = _replacement_guard(bpy)
        if refusal:
            return refusal
        path = Path(filepath).expanduser().resolve()
        before = _file_stamp(path)
        status = sorted(bpy.ops.wm.open_mainfile(filepath=filepath, load_ui=False))
        if "FINISHED" not in status:
            return skill_error("Scene was not opened", "open_incomplete", operator_status=status, filepath=filepath)
        actual_path = bpy.data.filepath
        if Path(actual_path).resolve() != Path(filepath).resolve():
            return skill_error(
                "Opened file could not be confirmed",
                "open_unconfirmed",
                operator_status=status,
                filepath=filepath,
                actual_filepath=actual_path,
            )
        _LAST_SAVE.clear()
        _LAST_OPEN.clear()
        after = _file_stamp(path)
        if before is not None and after == before and after["size"] > 0 and can_confirm_save():
            _LAST_OPEN.update(filepath=str(path), file_observation=after, write_revision=current_revision())
            saved_through(current_revision())
        return skill_success("Scene opened", filepath=actual_path, operator_status=status)
    except Exception as exc:
        return skill_exception(exc, message="Failed to open scene")


def _file_stamp(path):
    try:
        stat = path.stat()
        return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    except FileNotFoundError:
        return None


def save_scene(filepath=None) -> dict:
    """Save once and confirm the current file, without replaying uncertain writes."""
    refusal = guard_main_thread("save_scene")
    if refusal:
        return refusal
    target = filepath
    status = []
    try:
        import bpy

        target = filepath or bpy.data.filepath
        if not target:
            return skill_error("Provide a filepath for the first save", "filepath_required", save_status="failed")
        path = Path(target).expanduser().resolve()
        before = _file_stamp(path)
        _LAST_OPEN.clear()
        _LAST_SAVE.clear()
        _LAST_SAVE.update(
            filepath=str(path),
            before=before,
            operator_status=[],
            save_status="unknown",
            write_revision=current_revision(),
            confirmable=can_confirm_save(),
        )
        if filepath:
            status = sorted(bpy.ops.wm.save_as_mainfile(filepath=str(path)))
        else:
            status = sorted(bpy.ops.wm.save_mainfile())
        _LAST_SAVE["operator_status"] = status
        if "FINISHED" not in status:
            _LAST_SAVE["save_status"] = "pending" if "RUNNING_MODAL" in status else "failed"
            return skill_error(
                "Save did not finish",
                "save_incomplete",
                filepath=str(path),
                operator_status=status,
                save_status="pending" if "RUNNING_MODAL" in status else "failed",
            )
        return _observe_save(bpy, path, before, status)
    except Exception as exc:
        return skill_exception(
            exc,
            message="Save state is unknown",
            filepath=str(target),
            save_status="unknown",
            operator_status=status,
            prompt="Inspect current file state before another write; do not automatically retry this save.",
        )


def _preserve_mcp_dispatcher() -> None:
    """Re-register the MCP main-thread dispatcher timer after Blender factory reset.

    ``bpy.ops.wm.read_factory_settings(use_empty=True)`` unregisters all
    ``bpy.app.timers`` callbacks including the dispatcher pump.  The HTTP
    server stays alive (port remains open) but subsequent tool calls hang
    because the queue is never drained.

    This is best-effort — it must not raise or the ``new_scene`` operation
    would fail with a half-applied factory reset.
    """
    try:
        from dcc_mcp_blender.server import get_server

        server = get_server()
        if server is None:
            return

        dispatcher = getattr(server, "_blender_dispatcher", None)
        if dispatcher is None:
            return

        start_pump = getattr(dispatcher, "start_pump", None)
        if callable(start_pump):
            start_pump()
            logger.info("new_scene: re-registered MCP dispatcher timer after factory reset")

        # Re-run the readiness probe so /v1/readyz reflects the restored pump.
        readiness = getattr(server, "readiness", None)
        if readiness is not None and hasattr(readiness, "revalidate_dispatcher"):
            readiness.revalidate_dispatcher()
    except Exception:
        # Best-effort: never let dispatcher preservation break new_scene.
        logger.debug("new_scene: dispatcher preservation skipped", exc_info=True)


def new_scene() -> dict:
    """Create an empty file after resolving edits in the current file."""
    refusal = guard_main_thread("new_scene")
    if refusal:
        return refusal
    try:
        import bpy

        refusal = _replacement_guard(bpy)
        if refusal:
            return refusal
        try:
            status = sorted(bpy.ops.wm.read_factory_settings(use_empty=True))
        finally:
            _preserve_mcp_dispatcher()
        if "FINISHED" not in status:
            return skill_error("New scene did not finish", "new_scene_incomplete", operator_status=status)
        _LAST_SAVE.clear()
        _LAST_OPEN.clear()
        saved_through(current_revision())
        return skill_success("New scene created", operator_status=status)
    except Exception as exc:
        return skill_exception(exc, message="Failed to create new scene")


def close_scene() -> dict:
    """Request explicit shutdown only when the current edits are preserved."""
    refusal = guard_main_thread("close_scene")
    if refusal:
        return refusal
    try:
        import bpy

        refusal = _replacement_guard(bpy)
        if refusal:
            return refusal
        status = sorted(bpy.ops.wm.quit_blender())
        if "FINISHED" not in status:
            return skill_error("Close did not finish", "close_incomplete", operator_status=status)
        return skill_success("Close requested", close_status="requested", operator_status=status)
    except Exception as exc:
        return skill_exception(exc, message="Close state is unknown", close_status="unknown")


def file_state() -> dict:
    """Observe a confirmed save or stable typed open without writing the file."""
    import bpy

    refusal = guard_main_thread("file_state")
    if refusal:
        return refusal
    target = _LAST_SAVE.get("filepath") or _LAST_OPEN.get("filepath")
    observed = _file_stamp(Path(target)) if target else None
    current = bpy.data.filepath
    status = _LAST_SAVE.get("save_status", "unknown")
    source = None
    dirty_images = [
        image.name for image in bpy.data.images if image.is_dirty and image.type not in {"RENDER_RESULT", "COMPOSITING"}
    ]
    same_file = bool(current and target) and Path(current).resolve() == Path(target).resolve()
    if (
        not same_file
        or bpy.data.is_dirty
        or dirty_images
        or _LAST_SAVE.get("write_revision") != current_revision()
        or not _LAST_SAVE.get("confirmable", False)
    ):
        status = "unknown"
    elif (
        "FINISHED" in _LAST_SAVE.get("operator_status", [])
        and observed is not None
        and observed["size"] > 0
        and observed != _LAST_SAVE.get("before")
    ):
        status = "saved"
        source = "save"
        saved_through(_LAST_SAVE["write_revision"])
    if (
        not _LAST_SAVE
        and same_file
        and not bpy.data.is_dirty
        and not dirty_images
        and not pending_write()
        and _LAST_OPEN.get("write_revision") == current_revision()
        and observed is not None
        and observed == _LAST_OPEN.get("file_observation")
    ):
        status, source = "saved", "open"
    return dict(
        filepath=current,
        scene_dirty=bool(bpy.data.is_dirty),
        pending_managed_write=pending_write(),
        save_status=status,
        state_source=source,
        last_save=dict(_LAST_SAVE),
        last_open=dict(_LAST_OPEN),
        dirty_images=dirty_images,
        file_observation=observed,
    )


def _observe_save(bpy, path, before, status):
    after = _file_stamp(path)
    actual = bpy.data.filepath
    confirmed = (
        can_confirm_save()
        and bool(actual)
        and Path(actual).resolve() == path
        and not bpy.data.is_dirty
        and after is not None
        and after["size"] > 0
        and after != before
    )
    context = dict(
        filepath=str(path),
        actual_filepath=actual,
        scene_dirty=bool(bpy.data.is_dirty),
        operator_status=status,
        file_observation=after,
    )
    _LAST_SAVE["save_status"] = "saved" if confirmed else "pending"
    if not confirmed:
        return skill_error(
            "Save not yet confirmed",
            "save_unconfirmed",
            save_status="pending",
            prompt="Inspect current file state before another write; do not automatically retry this save.",
            **context,
        )
    saved_through(_LAST_SAVE["write_revision"])
    return skill_success("Scene saved", save_status="saved", **context)


def guard_before_replacement():
    """Return the shared refusal before a project replaces the current scene."""
    refusal = guard_main_thread("replace_scene")
    if refusal:
        return refusal
    import bpy

    return _replacement_guard(bpy)


def file_stamp(filepath):
    """Observe a file before handing it to a project-owned save operation."""
    return _file_stamp(Path(filepath).expanduser().resolve())


def observe_external_save(filepath, before_stamp, operator_status):
    """Record a project-owned save using the same current-file confirmation."""
    refusal = guard_main_thread("observe_external_save")
    if refusal:
        return refusal
    try:
        import bpy

        path = Path(filepath).expanduser().resolve()
        status = sorted(operator_status)
        _LAST_OPEN.clear()
        _LAST_SAVE.clear()
        _LAST_SAVE.update(
            filepath=str(path),
            before=before_stamp,
            operator_status=status,
            save_status="unknown",
            write_revision=current_revision(),
            confirmable=can_confirm_save(),
        )
        if "FINISHED" not in status:
            return skill_error(
                "External save did not finish", "save_incomplete", save_status="unknown", operator_status=status
            )
        return _observe_save(bpy, path, before_stamp, status)
    except Exception as exc:
        return skill_exception(exc, message="External save state is unknown", save_status="unknown")
