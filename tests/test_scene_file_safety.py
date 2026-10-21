"""File lifecycle behavior at the public scene/script entry points."""

# Keep the shared module loaded across load_and_call's temporary sys.modules patches.
from dcc_mcp_blender import _scene_files  # noqa: F401
from tests.conftest import load_and_call, make_mock_bpy


def test_open_refuses_unsaved_edits_without_touching_file_or_view():
    bpy = make_mock_bpy(data_attrs={"is_dirty": True})
    result = load_and_call("blender-scene/scripts/open_scene.py", bpy, filepath="/tmp/template.blend")
    assert result["success"] is False
    assert result["error"] == "unsaved_changes"
    bpy.ops.wm.open_mainfile.assert_not_called()
    bpy.ops.wm.save_mainfile.assert_not_called()


def test_save_reports_cancelled_operator_as_failure():
    bpy = make_mock_bpy()
    bpy.ops.wm.save_as_mainfile.return_value = {"CANCELLED"}
    result = load_and_call("blender-scene/scripts/save_scene.py", bpy, filepath="/tmp/out.blend")
    assert result["success"] is False
    assert result["context"]["operator_status"] == ["CANCELLED"]
    assert result["context"]["save_status"] == "failed"


def test_new_scene_refuses_dirty_image_pixels():
    from types import SimpleNamespace

    image = SimpleNamespace(name="Painted", is_dirty=True, type="IMAGE")
    bpy = make_mock_bpy(data_attrs={"images": [image]})
    result = load_and_call("blender-scene/scripts/new_scene.py", bpy)
    assert result["success"] is False
    assert result["context"]["dirty_images"] == ["Painted"]
    bpy.ops.wm.read_factory_settings.assert_not_called()


def test_finished_save_cannot_use_historical_file_as_proof(tmp_path):
    target = tmp_path / "old.blend"
    target.write_bytes(b"old data")
    bpy = make_mock_bpy(data_attrs={"filepath": str(target)})
    bpy.ops.wm.save_mainfile.return_value = {"FINISHED"}
    result = load_and_call("blender-scene/scripts/save_scene.py", bpy)
    assert result["success"] is False
    assert result["context"]["save_status"] == "pending"


def test_successful_save_preserves_dirty_image_and_later_close_refuses(tmp_path):
    from types import SimpleNamespace

    target = tmp_path / "current.blend"
    image = SimpleNamespace(name="Painted", is_dirty=True, type="IMAGE")
    bpy = make_mock_bpy(data_attrs={"images": [image], "is_dirty": True})

    def save(**kwargs):
        target.write_bytes(b"fresh state")
        bpy.data.filepath = str(target)
        bpy.data.is_dirty = False
        return {"FINISHED"}

    bpy.ops.wm.save_as_mainfile.side_effect = save
    result = load_and_call("blender-scene/scripts/save_scene.py", bpy, filepath=str(target))
    assert result["success"] is True
    assert result["context"]["save_status"] == "saved"
    assert image.is_dirty
    closed = load_and_call("blender-scene/scripts/close_scene.py", bpy)
    assert closed["success"] is False
    bpy.ops.wm.quit_blender.assert_not_called()


def test_file_actions_reject_background_thread():
    from concurrent.futures import ThreadPoolExecutor

    bpy = make_mock_bpy()
    with ThreadPoolExecutor(1) as pool:
        result = pool.submit(load_and_call, "blender-scene/scripts/new_scene.py", bpy).result()
    assert result["error"] == "main_thread_required"
    bpy.ops.wm.read_factory_settings.assert_not_called()


def test_save_exception_after_write_reports_unknown_without_retry(tmp_path):
    bpy = make_mock_bpy()
    target = tmp_path / "partially-written.blend"

    def failed_save(**kwargs):
        target.write_bytes(b"partial")
        raise RuntimeError("connection lost after write")

    bpy.ops.wm.save_as_mainfile.side_effect = failed_save
    result = load_and_call("blender-scene/scripts/save_scene.py", bpy, filepath=str(target))
    assert result["success"] is False
    assert result["context"]["save_status"] == "unknown"
    bpy.ops.wm.save_as_mainfile.assert_called_once()


def test_new_scene_ignores_disposable_render_result():
    from types import SimpleNamespace

    image = SimpleNamespace(name="Render Result", is_dirty=True, type="RENDER_RESULT")
    bpy = make_mock_bpy(data_attrs={"images": [image]})
    bpy.ops.wm.read_factory_settings.return_value = {"FINISHED"}
    result = load_and_call("blender-scene/scripts/new_scene.py", bpy)
    assert result["success"] is True


def test_open_keeps_user_layout_and_confirms_actual_file():
    bpy = make_mock_bpy(data_attrs={"filepath": "/tmp/actual.blend"})
    bpy.ops.wm.open_mainfile.return_value = {"FINISHED"}
    result = load_and_call("blender-scene/scripts/open_scene.py", bpy, filepath="/tmp/expected.blend")
    assert result["success"] is False
    assert result["error"] == "open_unconfirmed"
    bpy.ops.wm.open_mainfile.assert_called_once_with(filepath="/tmp/expected.blend", load_ui=False)


def test_explicit_close_reports_request_not_process_exit():
    bpy = make_mock_bpy()
    bpy.ops.wm.quit_blender.return_value = {"FINISHED"}
    result = load_and_call("blender-scene/scripts/close_scene.py", bpy)
    assert result["success"] is True
    assert result["context"]["close_status"] == "requested"


def test_pending_save_can_be_observed_without_another_write(tmp_path):
    target = tmp_path / "delayed.blend"
    target.write_bytes(b"old")
    bpy = make_mock_bpy(data_attrs={"filepath": str(target)})
    bpy.ops.wm.save_mainfile.return_value = {"FINISHED"}
    pending = load_and_call("blender-scene/scripts/save_scene.py", bpy)
    assert pending["context"]["save_status"] == "pending"
    target.write_bytes(b"completed save")
    observed = load_and_call("blender-scene/scripts/get_session_info.py", bpy)
    assert observed["context"]["file_state"]["save_status"] == "saved"
    bpy.ops.wm.save_mainfile.assert_called_once()
