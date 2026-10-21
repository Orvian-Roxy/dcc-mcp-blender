"""Main-thread file lifecycle operations shared by typed tools and project code.

Trusted Python can call bpy directly; this module is not a Python sandbox.
"""

from __future__ import annotations

import logging
import os
import stat
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
        observed = path.stat()
        if not stat.S_ISREG(observed.st_mode):
            return None
        return {"size": observed.st_size, "mtime_ns": observed.st_mtime_ns}
    except FileNotFoundError:
        return None


def _absolute_path(bpy, path):
    """Resolve Blender's // syntax before Save As changes the relative base."""
    return os.path.abspath(bpy.path.abspath(path))


def _same_destination(left, right):
    if os.path.normcase(os.path.realpath(left)) == os.path.normcase(os.path.realpath(right)):
        return True
    try:
        return os.path.samefile(left, right)
    except OSError:
        return False


def _save_failure(code, detail, attempted, *, save_status=None, **context):
    return skill_error(
        "Failed to save scene",
        detail,
        error_code=code,
        save_attempted=attempted,
        mutation_applied=None if attempted else False,
        save_status=save_status or ("unknown" if attempted else "failed"),
        prompt="Inspect the actual scene path and target file before retrying. No automatic retry or rollback was performed.",
        **context,
    )


def save_scene(filepath=None) -> dict:
    """Save once and confirm the native file and managed revision without replay."""
    refusal = guard_main_thread("save_scene")
    if refusal:
        return refusal
    attempted = False
    status = []
    if filepath is not None and (not isinstance(filepath, str) or not filepath.strip() or "\x00" in filepath):
        return _save_failure("invalid_filepath", "filepath must be a non-empty path string or omitted.", False)
    try:
        import bpy

        requested = filepath if filepath is not None else bpy.data.filepath
        if not isinstance(requested, str) or not requested:
            return _save_failure(
                "save_path_required", "The scene has not been saved yet. Please provide a filepath.", False
            )
        destination = _absolute_path(bpy, requested)
        expected_paths = [destination]
        if filepath is not None and not destination.lower().endswith(".blend"):
            expected_paths.append(destination + ".blend")
        before = {}
        observable = True
        for path in expected_paths:
            try:
                before[path] = _file_stamp(Path(path))
            except OSError:
                # Do not skip the native operation because its earlier metadata
                # could not be read, but do not claim a changed file afterward.
                before[path] = None
                observable = False
        _LAST_OPEN.clear()
        _LAST_SAVE.clear()
        _LAST_SAVE.update(
            filepath=destination,
            before=before[destination],
            before_candidates=before,
            operator_status=[],
            save_status="unknown",
            write_revision=current_revision(),
            confirmable=can_confirm_save() and observable,
        )
        attempted = True
        result = (
            bpy.ops.wm.save_as_mainfile(filepath=destination) if filepath is not None else bpy.ops.wm.save_mainfile()
        )
        status = (
            sorted(result)
            if isinstance(result, set) and all(isinstance(item, str) for item in result)
            else type(result).__name__
        )
        _LAST_SAVE["operator_status"] = status
        if not isinstance(result, set) or result != {"FINISHED"}:
            _LAST_SAVE["save_status"] = "pending" if "RUNNING_MODAL" in status else "failed"
            return _save_failure(
                "save_not_finished",
                "Blender did not report an unambiguous FINISHED status; an existing target file is not proof of a successful save.",
                True,
                filepath=destination,
                operator_result=status,
                operator_status=status,
                save_status=_LAST_SAVE["save_status"],
            )
        return _observe_save(
            bpy, Path(destination), before[destination], status, requested_path=requested, expected_paths=expected_paths
        )
    except ImportError:
        return _save_failure("blender_unavailable", "bpy could not be imported.", attempted)
    except Exception as exc:
        return _save_failure(
            "save_failed" if attempted else "save_preflight_failed",
            str(exc),
            attempted,
            error_type=type(exc).__name__,
            operator_result=status,
            operator_status=status,
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
        _LAST_SAVE.get("operator_status") == ["FINISHED"]
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


def _observe_save(bpy, path, before, status, *, requested_path=None, expected_paths=None):
    actual = bpy.data.filepath
    if not isinstance(actual, str) or not actual:
        return _save_failure(
            "save_path_unverified",
            "Blender did not report a saved scene filepath.",
            True,
            operator_result=status,
            operator_status=status,
        )
    actual = _absolute_path(bpy, actual)
    matched = next((target for target in expected_paths or [str(path)] if _same_destination(actual, target)), None)
    if matched is None:
        return _save_failure(
            "save_path_mismatch",
            "Blender's saved scene filepath does not match the requested destination.",
            True,
            operator_result=status,
            operator_status=status,
            requested_filepath=requested_path or str(path),
            actual_filepath=actual,
        )
    before = _LAST_SAVE.get("before_candidates", {}).get(matched, before)
    _LAST_SAVE.update(filepath=actual, before=before)
    saved_stat = os.stat(actual)
    if not stat.S_ISREG(saved_stat.st_mode) or saved_stat.st_size <= 0:
        return _save_failure(
            "save_file_unverified",
            "The reported destination is not a nonempty regular file.",
            True,
            operator_result=status,
            operator_status=status,
            actual_filepath=actual,
        )
    after = {"size": saved_stat.st_size, "mtime_ns": saved_stat.st_mtime_ns}
    confirmed = (
        status == ["FINISHED"]
        and can_confirm_save()
        and _LAST_SAVE.get("confirmable", False)
        and not bpy.data.is_dirty
        and after != before
    )
    context = dict(
        filepath=actual,
        requested_filepath=requested_path or str(path),
        actual_filepath=actual,
        scene_dirty=bool(bpy.data.is_dirty),
        operator_result=status,
        operator_status=status,
        file_observation=after,
        file_size_bytes=saved_stat.st_size,
    )
    _LAST_SAVE["save_status"] = "saved" if confirmed else "pending"
    if not confirmed:
        return _save_failure(
            "save_unconfirmed",
            "Save not yet confirmed; observe current file state before another write.",
            True,
            save_status="pending",
            **context,
        )
    saved_through(_LAST_SAVE["write_revision"])
    return skill_success(
        f"Scene saved to {actual}",
        save_status="saved",
        verified=True,
        postcondition={"method": "finished_operator_matching_scene_path_nonempty_file"},
        prompt="Save operator, scene path and nonempty file verified. Reopen separately when content integrity must be established.",
        **context,
    )


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
        if status != ["FINISHED"]:
            return skill_error(
                "External save did not finish", "save_incomplete", save_status="unknown", operator_status=status
            )
        return _observe_save(bpy, path, before_stamp, status)
    except Exception as exc:
        return skill_exception(exc, message="External save state is unknown", save_status="unknown")
