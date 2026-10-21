"""Managed host execution protects writes even without Blender undo events."""

import importlib
import sys
import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import yaml
from dcc_mcp_core import HostExecutionBridge

from dcc_mcp_blender._managed_writes import install_write_guard
from dcc_mcp_blender.host import BlenderCallableDispatcher
from tests.conftest import load_and_call, make_mock_bpy


@pytest.fixture(autouse=True)
def fresh_file_observations(monkeypatch):
    # Preload outside patch.dict(sys.modules): scripts and tests must share one module.
    files = importlib.import_module("dcc_mcp_blender._scene_files")
    monkeypatch.setattr(files, "_LAST_SAVE", {})
    monkeypatch.setattr(files, "_LAST_OPEN", {})


def bridge_for(dispatcher=None):
    bridge = HostExecutionBridge(dispatcher=dispatcher)
    tools = [SimpleNamespace(name="observe", read_only=True)]
    install_write_guard(bridge, lambda name: SimpleNamespace(tools=tools))
    return bridge


@pytest.mark.parametrize("already_pending", [False, True])
def test_typed_session_observes_without_changing_managed_revision(already_pending):
    from pathlib import Path

    from dcc_mcp_blender import _managed_writes as writes

    root = Path(__file__).parents[1] / "src/dcc_mcp_blender/skills/blender-scene"
    tools = [SimpleNamespace(**tool) for tool in yaml.safe_load((root / "tools.yaml").read_text())["tools"]]
    bridge = HostExecutionBridge()
    install_write_guard(bridge, lambda name: SimpleNamespace(tools=tools))
    if already_pending:
        bridge.dispatch_callable(lambda: None, action_name="unknown-write", skill_name="test")
    before, pending = writes.current_revision(), writes.pending_write()
    bpy = make_mock_bpy()
    bpy.app.is_job_running.return_value = False
    bpy.context.window_manager.windows = []
    with patch.dict(sys.modules, {"bpy": bpy}):
        result = bridge.execute_script(
            str(root / "scripts/get_session_info.py"),
            {},
            action_name="blender_scene__get_session_info",
            skill_name="blender-scene",
        )
    assert result["success"] is True
    assert result["context"]["file_state"]["pending_managed_write"] is pending
    assert writes.current_revision() == before
    assert writes.pending_write() is pending


@pytest.mark.parametrize("invalidate", [None, "scene", "pixels", "write", "path", "file"])
def test_typed_open_observation_reports_persisted_file_without_resaving(tmp_path, invalidate):
    from dcc_mcp_blender import _scene_files as files

    bridge = bridge_for()
    target = tmp_path / "loaded.blend"
    target.write_bytes(b"existing saved project")
    bpy = make_mock_bpy()

    def load(**kwargs):
        bpy.data.filepath = kwargs["filepath"]
        return {"FINISHED"}

    bpy.ops.wm.open_mainfile.side_effect = load
    opened = bridge.dispatch_callable(
        lambda: load_and_call("blender-scene/scripts/open_scene.py", bpy, filepath=str(target)),
        action_name="blender_scene__open_scene",
        skill_name="blender-scene",
    )
    assert opened["success"]
    if invalidate == "scene":
        bpy.data.is_dirty = True
    elif invalidate == "pixels":
        bpy.data.images = [SimpleNamespace(name="Edited", is_dirty=True, type="IMAGE")]
    elif invalidate == "write":
        bridge.dispatch_callable(lambda: None, action_name="unknown-write", skill_name="test")
    elif invalidate == "path":
        bpy.data.filepath = str(tmp_path / "other.blend")
    elif invalidate == "file":
        target.write_bytes(b"external replacement of saved project")
    with patch.dict(sys.modules, {"bpy": bpy}):
        observed = files.file_state()
    assert (observed["save_status"] == "saved") is (invalidate is None)
    if invalidate is None:
        assert observed["state_source"] == "open"
        assert observed["last_save"] == {}
        assert not observed["pending_managed_write"]
    bpy.ops.wm.save_mainfile.assert_not_called()
    bpy.ops.wm.save_as_mainfile.assert_not_called()


def test_new_failed_save_does_not_fall_back_to_previous_open_observation(tmp_path):
    bridge = bridge_for()
    target = tmp_path / "loaded.blend"
    target.write_bytes(b"existing project")
    bpy = make_mock_bpy(data_attrs={"filepath": str(target)})
    bpy.ops.wm.open_mainfile.return_value = {"FINISHED"}
    bridge.dispatch_callable(
        lambda: load_and_call("blender-scene/scripts/open_scene.py", bpy, filepath=str(target)),
        action_name="blender_scene__open_scene",
        skill_name="blender-scene",
    )
    bpy.ops.wm.save_mainfile.return_value = {"CANCELLED"}
    saved = bridge.dispatch_callable(
        lambda: load_and_call("blender-scene/scripts/save_scene.py", bpy),
        action_name="blender_scene__save_scene",
        skill_name="blender-scene",
    )
    assert not saved["success"]
    observed = load_and_call("blender-scene/scripts/get_session_info.py", bpy)["context"]["file_state"]
    assert observed["save_status"] != "saved"
    assert observed["pending_managed_write"]


def test_opened_file_changed_during_load_remains_unconfirmed(tmp_path):
    bridge = bridge_for()
    target = tmp_path / "raced.blend"
    target.write_bytes(b"old saved data")
    bpy = make_mock_bpy()

    def load(**kwargs):
        bpy.data.filepath = kwargs["filepath"]
        target.write_bytes(b"external replacement during load")
        return {"FINISHED"}

    bpy.ops.wm.open_mainfile.side_effect = load
    bridge.dispatch_callable(
        lambda: load_and_call("blender-scene/scripts/open_scene.py", bpy, filepath=str(target)),
        action_name="blender_scene__open_scene",
        skill_name="blender-scene",
    )
    state = load_and_call("blender-scene/scripts/get_session_info.py", bpy)["context"]["file_state"]
    assert state["save_status"] == "unknown"
    assert state["last_open"] == {}
    assert state["pending_managed_write"]


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


@pytest.mark.parametrize("already_pending", [False, True])
@pytest.mark.parametrize("fail", [False, True])
def test_viewport_file_export_preserves_scene_write_state(tmp_path, already_pending, fail):
    from pathlib import Path

    from dcc_mcp_blender import _managed_writes

    manifest = Path(__file__).parents[1] / "src/dcc_mcp_blender/skills/blender-render/tools.yaml"
    tools = [SimpleNamespace(**tool) for tool in yaml.safe_load(manifest.read_text())["tools"]]
    assert next(tool for tool in tools if tool.name == "capture_viewport").read_only is False
    bridge = HostExecutionBridge()
    install_write_guard(bridge, lambda name: SimpleNamespace(tools=tools))
    if already_pending:
        bridge.dispatch_callable(lambda: None, action_name="unknown-write", skill_name="test")
    before = _managed_writes.current_revision()
    pending = _managed_writes.pending_write()
    script = tmp_path / "capture.py"
    script.write_text(f"def main(**kwargs):\n    return {{'success': {not fail}}}\n")
    result = bridge.execute_script(
        str(script), {}, action_name="blender_render__capture_viewport", skill_name="blender-render"
    )
    assert result["success"] is not fail
    assert _managed_writes.current_revision() == before
    assert _managed_writes.pending_write() is pending


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


@pytest.mark.parametrize("game", ["wuwa", "WutheringWaves", "hsr", "HonkaiStarRail"])
@pytest.mark.parametrize("fail", [False, True])
@pytest.mark.parametrize("already_pending", [False, True])
def test_observational_verify_preserves_revision_even_on_failure(tmp_path, game, fail, already_pending):
    from dcc_mcp_blender import _managed_writes

    bridge = bridge_for()
    if already_pending:
        bridge.dispatch_callable(lambda: None, action_name="unknown-write", skill_name="test")
    before = _managed_writes.current_revision()
    pending = _managed_writes.pending_write()
    script = tmp_path / "verify.py"
    script.write_text(
        "def main(**kwargs):\n"
        + ("    raise RuntimeError('verification failed')\n" if fail else "    return {'success': True}\n")
    )
    result = bridge.execute_script(
        str(script),
        {"action": "verify", "game": game},
        action_name="blender_nodebpy_runtime__project",
        skill_name="blender-nodebpy-runtime",
    )
    assert result["success"] is not fail
    assert _managed_writes.current_revision() == before
    assert _managed_writes.pending_write() is pending


@pytest.mark.parametrize(
    "game,action",
    [
        ("genshin", "verify"),
        ("zzz", "verify"),
        ("WUWA", "verify"),
        ("HSR", "verify"),
        ("", "verify"),
        ("wuwa", "run"),
        ("hsr", "run"),
    ],
)
def test_other_project_calls_still_mark_potential_writes(tmp_path, game, action):
    from dcc_mcp_blender import _managed_writes

    bridge = bridge_for()
    before = _managed_writes.current_revision()
    script = tmp_path / "project.py"
    script.write_text("def main(**kwargs):\n    return {'success': True}\n")
    bridge.execute_script(
        str(script),
        {"action": action, "game": game},
        action_name="blender_nodebpy_runtime__project",
        skill_name="blender-nodebpy-runtime",
    )
    assert _managed_writes.current_revision() == before + 1
    assert _managed_writes.pending_write()


@pytest.mark.parametrize("game", ["genshin", "Genshin"])
@pytest.mark.parametrize("advertised", [False, True])
@pytest.mark.parametrize("fail", [False, True])
@pytest.mark.parametrize("already_pending", [False, True])
def test_gi_verify_uses_the_loaded_execute_capability(tmp_path, monkeypatch, game, advertised, fail, already_pending):
    from dcc_mcp_blender import _managed_writes

    def execute():
        pass

    if advertised:
        execute.read_only_verify_games = frozenset({"Genshin"})
    monkeypatch.setitem(sys.modules, "nodebpy_build", SimpleNamespace(execute=execute))
    bridge = bridge_for()
    if already_pending:
        bridge.dispatch_callable(lambda: None, action_name="unknown-write", skill_name="test")
    before = _managed_writes.current_revision()
    script = tmp_path / "gi_verify.py"
    script.write_text(
        "def main(**kwargs):\n"
        + ("    raise RuntimeError('verification failed')\n" if fail else "    return {'success': True}\n")
    )
    result = bridge.execute_script(
        str(script),
        {"action": "verify", "game": game, "read_only": True},
        action_name="blender_nodebpy_runtime__project",
        skill_name="blender-nodebpy-runtime",
    )
    assert result["success"] is not fail
    assert _managed_writes.current_revision() == before + (not advertised)
    assert _managed_writes.pending_write() is (already_pending or not advertised)


def test_gi_capability_does_not_survive_execute_replacement_or_exempt_run(tmp_path, monkeypatch):
    from dcc_mcp_blender import _managed_writes

    def execute():
        pass

    execute.read_only_verify_games = frozenset({"Genshin"})
    module = SimpleNamespace(execute=execute)
    monkeypatch.setitem(sys.modules, "nodebpy_build", module)
    bridge = bridge_for()
    script = tmp_path / "gi_project.py"
    script.write_text("def main(**kwargs):\n    return {'success': True}\n")
    before = _managed_writes.current_revision()
    for action in ("verify", "run"):
        bridge.execute_script(
            str(script),
            {"action": action, "game": "genshin"},
            action_name="blender_nodebpy_runtime__project",
            skill_name="blender-nodebpy-runtime",
        )
    assert _managed_writes.current_revision() == before + 1
    # Reloading older source redefines execute even when the module object remains.
    module.execute = lambda: None
    bridge.execute_script(
        str(script),
        {"action": "verify", "game": "genshin"},
        action_name="blender_nodebpy_runtime__project",
        skill_name="blender-nodebpy-runtime",
    )
    assert _managed_writes.current_revision() == before + 2


@pytest.mark.parametrize("game", ["wuwa", "hsr"])
@pytest.mark.parametrize("dirty_image", [False, True])
def test_observational_verify_does_not_bypass_scene_or_image_dirty_guard(tmp_path, dirty_image, game):
    bridge = bridge_for()
    bpy = make_mock_bpy()
    bpy.data.is_dirty = not dirty_image
    bpy.data.images = [SimpleNamespace(name="Edited", is_dirty=True, type="IMAGE")] if dirty_image else []
    script = tmp_path / "verify.py"
    script.write_text("def main(**kwargs):\n    return {'success': True}\n")
    bridge.execute_script(
        str(script),
        {"action": "verify", "game": game},
        action_name="blender_nodebpy_runtime__project",
        skill_name="blender-nodebpy-runtime",
    )
    refused = bridge.dispatch_callable(
        lambda: load_and_call("blender-scene/scripts/open_scene.py", bpy, filepath="/tmp/other.blend"),
        action_name="blender_scene__open_scene",
        skill_name="blender-scene",
    )
    assert refused["error"] == "unsaved_changes"
    bpy.ops.wm.open_mainfile.assert_not_called()


@pytest.mark.parametrize("already_pending", [False, True])
@pytest.mark.parametrize("outcome", ["success", "capture", "preflight", "restore", "exception", "missing", "untrusted"])
def test_preview_restoration_controls_only_its_own_revision(already_pending, outcome):
    from dcc_mcp_blender import _managed_writes as writes

    bridge = HostExecutionBridge()
    install_write_guard(
        bridge, lambda name: SimpleNamespace(tools=[SimpleNamespace(name="preview_variants", read_only=False)])
    )
    if already_pending:
        bridge.dispatch_callable(lambda: None, action_name="edit", skill_name="test")
    before, saved = writes.current_revision(), writes._saved_revision

    def preview():
        assert writes.pending_write()
        assert not writes.can_confirm_save()
        writes.saved_through(writes.current_revision())
        if outcome == "exception":
            raise RuntimeError("preview failed")
        receipt = {"restored": True, "mutated": False}
        if outcome == "restore":
            receipt = {"restored": False, "mutated": True}
        elif outcome == "missing":
            receipt = {}
        elif outcome == "untrusted":
            receipt = {"restored": 1, "mutated": 0}
        return {"success": outcome == "success", "context": receipt}

    try:
        bridge.dispatch_callable(
            preview, action_name="blender_node_runtime__preview_variants", skill_name="blender-node-runtime"
        )
    except RuntimeError:
        assert outcome == "exception"
    changed = outcome not in {"success", "capture", "preflight"}
    assert writes.current_revision() == before + changed
    assert writes._saved_revision == saved
    assert writes.pending_write() is (already_pending or changed)


def test_restored_preview_keeps_nested_writes_and_requires_declaration():
    from dcc_mcp_blender import _managed_writes as writes

    for declared in (True, False):
        bridge = HostExecutionBridge()
        tools = [SimpleNamespace(name="preview_variants", read_only=False)] if declared else []
        install_write_guard(bridge, lambda name: SimpleNamespace(tools=tools))
        before = writes.current_revision()

        def preview():
            bridge.dispatch_callable(lambda: None, action_name="edit", skill_name="test")
            return {"success": True, "context": {"restored": True, "mutated": False}}

        bridge.dispatch_callable(
            preview, action_name="blender_node_runtime__preview_variants", skill_name="blender-node-runtime"
        )
        assert writes.current_revision() == before + (1 if declared else 2)
        assert writes.pending_write()
