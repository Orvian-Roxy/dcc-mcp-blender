"""Managed host execution protects writes even without Blender undo events."""

import sys
import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from dcc_mcp_core import HostExecutionBridge

from dcc_mcp_blender._managed_writes import install_write_guard
from dcc_mcp_blender.host import BlenderCallableDispatcher
from tests.conftest import load_and_call, make_mock_bpy


def bridge_for(dispatcher=None):
    bridge = HostExecutionBridge(dispatcher=dispatcher)
    tools = [SimpleNamespace(name="observe", read_only=True)]
    install_write_guard(bridge, lambda name: SimpleNamespace(tools=tools))
    return bridge


@pytest.mark.parametrize("fast_path", [False, True])
def test_managed_python_write_blocks_next_typed_open_without_dirty_flag(fast_path):
    dispatcher = SimpleNamespace(is_host_thread=lambda: True) if fast_path else None
    bridge = bridge_for(dispatcher)
    bpy = make_mock_bpy()
    bridge.dispatch_callable(
        lambda: setattr(bpy.context.scene, "name", "Agent edited"), action_name="edit", skill_name="test"
    )
    assert bpy.data.is_dirty is False
    refused = bridge.dispatch_callable(
        lambda: load_and_call("blender-scene/scripts/open_scene.py", bpy, filepath="/tmp/template.blend"),
        action_name="blender_scene__open_scene",
        skill_name="blender-scene",
    )
    assert refused["error"] == "unsaved_changes"
    assert refused["context"]["pending_managed_write"] is True
    bpy.ops.wm.open_mainfile.assert_not_called()


def test_readonly_and_initial_file_action_do_not_self_block():
    bridge = bridge_for()
    bpy = make_mock_bpy(data_attrs={"filepath": "/tmp/template.blend"})
    bpy.ops.wm.open_mainfile.return_value = {"FINISHED"}
    bridge.dispatch_callable(lambda: None, action_name="observe", skill_name="test")
    opened = bridge.dispatch_callable(
        lambda: load_and_call("blender-scene/scripts/open_scene.py", bpy, filepath="/tmp/template.blend"),
        action_name="blender_scene__open_scene",
        skill_name="blender-scene",
    )
    assert opened["success"] is True


def test_queued_write_is_marked_on_actual_main_thread():
    dispatcher = BlenderCallableDispatcher()
    bridge = bridge_for(dispatcher)
    bpy = make_mock_bpy()
    observed = []
    worker = threading.Thread(
        target=lambda: bridge.dispatch_callable(
            lambda: observed.append(threading.current_thread() is threading.main_thread()),
            action_name="edit",
            skill_name="test",
        )
    )
    worker.start()
    while worker.is_alive():
        dispatcher.tick_blocking(1, 10)
    worker.join(timeout=1)
    assert observed == [True]
    refused = load_and_call("blender-scene/scripts/new_scene.py", bpy)
    assert refused["error"] == "unsaved_changes"


def test_script_batch_partial_error_keeps_pending_until_save(tmp_path):
    bridge = bridge_for()
    bpy = make_mock_bpy()
    with patch.dict(sys.modules, {"bpy": bpy}):
        script = tmp_path / "batch.py"
        script.write_text(
            "def main(**kwargs):\n    import bpy\n    bpy.context.scene.name = 'Changed'\n    raise RuntimeError('partial write')\n"
        )
        bridge.execute_script(str(script), {}, action_name="script", skill_name="test")
    assert load_and_call("blender-scene/scripts/new_scene.py", bpy)["error"] == "unsaved_changes"
    target = tmp_path / "saved.blend"

    def save(**kwargs):
        target.write_bytes(b"current state")
        bpy.data.filepath = str(target)
        return {"FINISHED"}

    bpy.ops.wm.save_as_mainfile.side_effect = save
    saved = bridge.dispatch_callable(
        lambda: load_and_call("blender-scene/scripts/save_scene.py", bpy, filepath=str(target)),
        action_name="blender_scene__save_scene",
        skill_name="blender-scene",
    )
    assert saved["context"]["save_status"] == "saved"
    bpy.ops.wm.read_factory_settings.return_value = {"FINISHED"}
    assert (
        bridge.dispatch_callable(
            lambda: load_and_call("blender-scene/scripts/new_scene.py", bpy),
            action_name="blender_scene__new_scene",
            skill_name="blender-scene",
        )["success"]
        is True
    )


def test_later_managed_write_invalidates_saved_observation(tmp_path):
    bridge = bridge_for()
    target = tmp_path / "saved.blend"
    bpy = make_mock_bpy(data_attrs={"filepath": str(target)})

    def save():
        target.write_bytes(b"new state")
        return {"FINISHED"}

    bpy.ops.wm.save_mainfile.side_effect = save
    bridge.dispatch_callable(
        lambda: load_and_call("blender-scene/scripts/save_scene.py", bpy), action_name="save", skill_name="test"
    )
    bridge.dispatch_callable(lambda: None, action_name="unknown-write", skill_name="test")
    observed = load_and_call("blender-scene/scripts/get_session_info.py", bpy)
    assert observed["context"]["file_state"]["save_status"] == "unknown"
    assert observed["context"]["file_state"]["pending_managed_write"] is True


def test_external_project_save_confirms_without_repeating_operator(tmp_path):
    from dcc_mcp_blender._scene_files import file_stamp, observe_external_save

    bpy = make_mock_bpy()
    target = tmp_path / "project.blend"
    before = file_stamp(target)
    bridge = bridge_for()
    bridge.dispatch_callable(lambda: None, action_name="project-run", skill_name="test")
    target.write_bytes(b"project saved this version")
    bpy.data.filepath = str(target)
    with patch.dict(sys.modules, {"bpy": bpy}):
        observed = observe_external_save(str(target), before, ["FINISHED"])
    assert observed["context"]["save_status"] == "saved"
    bpy.ops.wm.save_mainfile.assert_not_called()
    bpy.ops.wm.save_as_mainfile.assert_not_called()
    bpy.ops.wm.read_factory_settings.return_value = {"FINISHED"}
    assert load_and_call("blender-scene/scripts/new_scene.py", bpy)["success"] is True


def test_same_script_edit_cannot_replace_through_protected_helper():
    bridge = bridge_for()
    bpy = make_mock_bpy(data_attrs={"filepath": "/tmp/template.blend"})
    bpy.ops.wm.open_mainfile.return_value = {"FINISHED"}

    def script():
        bpy.context.scene.name = "Must survive"
        assert bpy.data.is_dirty is False
        return load_and_call("blender-scene/scripts/open_scene.py", bpy, filepath="/tmp/template.blend")

    result = bridge.dispatch_callable(
        script, action_name="blender_scripting__execute_python", skill_name="blender-scripting"
    )
    assert result["error"] == "unsaved_changes"
    bpy.ops.wm.open_mainfile.assert_not_called()


def test_generic_script_save_then_later_edit_stays_pending_for_next_typed_save(tmp_path):
    bridge = bridge_for()
    bpy = make_mock_bpy()
    target = tmp_path / "saved.blend"

    def save(**kwargs):
        target.write_bytes(b"saved script checkpoint")
        bpy.data.filepath = str(target)
        return {"FINISHED"}

    bpy.ops.wm.save_as_mainfile.side_effect = save

    def script():
        result = load_and_call("blender-scene/scripts/save_scene.py", bpy, filepath=str(target))
        bpy.context.scene.name = "Changed after save"
        assert bpy.data.is_dirty is False
        return result

    bridge.dispatch_callable(script, action_name="blender_scripting__execute_python", skill_name="blender-scripting")
    state = load_and_call("blender-scene/scripts/get_session_info.py", bpy)["context"]["file_state"]
    assert state["save_status"] != "saved"
    assert state["pending_managed_write"] is True
    assert load_and_call("blender-scene/scripts/open_scene.py", bpy, filepath=str(target))["error"] == "unsaved_changes"
    saved = bridge.dispatch_callable(
        lambda: load_and_call("blender-scene/scripts/save_scene.py", bpy, filepath=str(target)),
        action_name="blender_scene__save_scene",
        skill_name="blender-scene",
    )
    assert saved["context"]["save_status"] == "saved"
    state = load_and_call("blender-scene/scripts/get_session_info.py", bpy)["context"]["file_state"]
    assert state["save_status"] == "saved"
    assert state["pending_managed_write"] is False


@pytest.mark.parametrize("action, allowed", [("run", True), ("verify", False)])
def test_only_explicit_project_run_is_a_replacement_boundary(action, allowed, tmp_path):
    bridge = bridge_for()
    bpy = make_mock_bpy(data_attrs={"filepath": "/tmp/project.blend"})
    bpy.ops.wm.open_mainfile.return_value = {"FINISHED"}
    script = "from dcc_mcp_blender._scene_files import open_scene\ndef main(**kwargs):\n    return open_scene('/tmp/project.blend')\n"
    path = tmp_path / "project.py"
    path.write_text(script)
    with patch.dict(sys.modules, {"bpy": bpy}):
        result = bridge.execute_script(
            str(path),
            {"action": action},
            action_name="blender_nodebpy_runtime__project",
            skill_name="blender-nodebpy-runtime",
        )
    assert result["success"] is allowed
    if not allowed:
        assert result["error"] == "unsaved_changes"
        bpy.ops.wm.open_mainfile.assert_not_called()


@pytest.mark.parametrize("already_pending", [False, True])
def test_project_check_preserves_existing_write_state_without_creating_one(tmp_path, already_pending):
    bridge = bridge_for()
    target = tmp_path / "checked.blend"
    bpy = make_mock_bpy()

    def save(**kwargs):
        target.write_bytes(b"saved before check")
        bpy.data.filepath = str(target)
        return {"FINISHED"}

    bpy.ops.wm.save_as_mainfile.side_effect = save
    bridge.dispatch_callable(
        lambda: load_and_call("blender-scene/scripts/save_scene.py", bpy, filepath=str(target)),
        action_name="blender_scene__save_scene",
        skill_name="blender-scene",
    )
    if already_pending:
        bridge.dispatch_callable(lambda: None, action_name="unknown-write", skill_name="test")
    script = tmp_path / "check.py"
    script.write_text(
        "def main(**kwargs):\n    from dcc_mcp_blender._scene_files import file_state\n    return file_state()\n"
    )
    with patch.dict(sys.modules, {"bpy": bpy}):
        observed = bridge.execute_script(
            str(script),
            {"action": "check"},
            action_name="blender_nodebpy_runtime__project",
            skill_name="blender-nodebpy-runtime",
        )
    assert observed["pending_managed_write"] is already_pending
    assert (observed["save_status"] == "saved") is not already_pending
