"""Unit tests for blender-scene skill scripts (bpy mocked)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import load_and_call, make_mock_bpy


class TestGetSessionInfo:
    @pytest.mark.parametrize("playing", [False, True])
    def test_observes_exact_pid_jobs_playback_and_dirty_images(self, playing):
        bpy = make_mock_bpy()
        bpy.app.is_job_running.side_effect = lambda kind: kind == "SHADER_COMPILATION"
        bpy.context.window_manager.windows = [
            SimpleNamespace(screen=None),
            SimpleNamespace(screen=SimpleNamespace(is_animation_playing=playing)),
        ]
        bpy.data.images = [SimpleNamespace(name="Edited pixels", is_dirty=True, type="IMAGE")]
        with patch("os.getpid", return_value=12345):
            result = load_and_call("blender-scene/scripts/get_session_info.py", bpy)
        assert result["success"] is True
        state = result["context"]
        assert state["pid"] == 12345
        assert state["jobs"] == dict.fromkeys(("RENDER", "RENDER_PREVIEW", "OBJECT_BAKE", "COMPOSITE"), False) | {
            "SHADER_COMPILATION": True
        }
        assert state["job_errors"] == {}
        assert state["animation_playing"] is playing
        assert state["file_state"]["dirty_images"] == ["Edited pixels"]
        bpy.app.is_job_running.assert_any_call("OBJECT_BAKE")
        bpy.ops.wm.save_mainfile.assert_not_called()

    def test_unsupported_native_job_is_unknown_and_headless_playback_is_false(self):
        bpy = make_mock_bpy()
        bpy.context.window_manager.windows = []

        def observe(kind):
            if kind == "SHADER_COMPILATION":
                raise ValueError("unsupported job type")
            return False

        bpy.app.is_job_running.side_effect = observe
        result = load_and_call("blender-scene/scripts/get_session_info.py", bpy)
        assert result["success"] is True
        assert result["context"]["jobs"]["SHADER_COMPILATION"] is None
        assert result["context"]["job_errors"] == {"SHADER_COMPILATION": "ValueError: unsupported job type"}
        assert result["context"]["animation_playing"] is False

    def test_returns_version_info(self):
        bpy = make_mock_bpy(app_attrs={"version": (4, 1, 0), "version_string": "4.1.0"})
        result = load_and_call("blender-scene/scripts/get_session_info.py", bpy)
        assert result["success"] is True
        assert result["context"]["blender_version"] == "4.1.0"

    def test_no_bpy_returns_error(self):
        result = load_and_call(
            "blender-scene/scripts/get_session_info.py",
            mock_bpy_obj=None,
        )
        # Without bpy the script is patched with our default mock — just check structure
        assert "success" in result

    def test_context_has_expected_fields(self):
        bpy = make_mock_bpy()
        result = load_and_call("blender-scene/scripts/get_session_info.py", bpy)
        assert result["success"] is True
        ctx = result["context"]
        for field in ["blender_version", "scene_name", "object_count"]:
            assert field in ctx, f"Missing field: {field}"


class TestListObjects:
    def test_empty_scene(self):
        bpy = make_mock_bpy(data_attrs={"objects": []})
        result = load_and_call("blender-scene/scripts/list_objects.py", bpy)
        assert result["success"] is True
        assert result["context"]["count"] == 0

    def test_with_objects(self):
        obj1 = MagicMock()
        obj1.name = "Cube"
        obj1.type = "MESH"
        obj1.location = [0.0, 0.0, 0.0]
        obj1.hide_viewport = False

        obj2 = MagicMock()
        obj2.name = "Light"
        obj2.type = "LIGHT"
        obj2.location = [0.0, 0.0, 3.0]
        obj2.hide_viewport = False

        bpy = make_mock_bpy(data_attrs={"objects": [obj1, obj2]})
        result = load_and_call("blender-scene/scripts/list_objects.py", bpy)
        assert result["success"] is True
        assert result["context"]["count"] == 2

    def test_type_filter(self):
        obj1 = MagicMock()
        obj1.name = "Cube"
        obj1.type = "MESH"
        obj1.location = [0, 0, 0]
        obj1.hide_viewport = False

        obj2 = MagicMock()
        obj2.name = "Light"
        obj2.type = "LIGHT"
        obj2.location = [0, 0, 3]
        obj2.hide_viewport = False

        bpy = make_mock_bpy(data_attrs={"objects": [obj1, obj2]})
        result = load_and_call("blender-scene/scripts/list_objects.py", bpy, object_type="MESH")
        assert result["success"] is True
        names = [o["name"] for o in result["context"]["objects"]]
        assert "Cube" in names
        assert "Light" not in names


class TestNewScene:
    def test_calls_read_factory_settings(self):
        bpy = make_mock_bpy()
        result = load_and_call("blender-scene/scripts/new_scene.py", bpy)
        assert result["success"] is True
        bpy.ops.wm.read_factory_settings.assert_called_once_with(use_empty=True)


class TestSaveScene:
    def test_save_to_explicit_path(self, tmp_path):
        from tests.test_save_scene_contract import host

        bpy = host(tmp_path)
        path = str(tmp_path / "test.blend")
        result = load_and_call("blender-scene/scripts/save_scene.py", bpy, filepath=path)
        assert result["success"] is True
        bpy.ops.wm.save_as_mainfile.assert_called_once_with(filepath=path)

    def test_save_existing_file(self, tmp_path):
        from tests.test_save_scene_contract import host

        bpy = host(tmp_path)
        bpy.data.filepath = str(tmp_path / "existing.blend")
        result = load_and_call("blender-scene/scripts/save_scene.py", bpy)
        assert result["success"] is True
        bpy.ops.wm.save_mainfile.assert_called_once()

    def test_error_when_no_filepath_and_unsaved(self):
        bpy = make_mock_bpy()
        bpy.data.filepath = ""
        result = load_and_call("blender-scene/scripts/save_scene.py", bpy)
        assert result["success"] is False


class TestOpenScene:
    def test_opens_blend_file(self, tmp_path):
        path = tmp_path / "scene.blend"
        path.write_bytes(b"saved scene")
        bpy = make_mock_bpy()

        def load(**kwargs):
            bpy.data.filepath = kwargs["filepath"]
            return {"FINISHED"}

        bpy.ops.wm.open_mainfile.side_effect = load
        result = load_and_call("blender-scene/scripts/open_scene.py", bpy, filepath=str(path))
        assert result["success"] is True
        bpy.ops.wm.open_mainfile.assert_called_once_with(filepath=str(path), load_ui=False)


class TestGetSceneInfo:
    def test_returns_scene_stats(self):
        bpy = make_mock_bpy()
        # Mock collection hierarchy
        mock_col = MagicMock()
        mock_col.name = "Scene Collection"
        mock_col.objects = []
        mock_col.children = []
        bpy.context.scene.collection = mock_col
        bpy.context.scene.objects = []

        result = load_and_call("blender-scene/scripts/get_scene_info.py", bpy)
        assert result["success"] is True
        ctx = result["context"]
        assert "scene_name" in ctx
        assert "collections" in ctx

    def test_counts_objects_by_type_and_recurses_collections(self):
        mesh = MagicMock()
        mesh.name = "Cube"
        mesh.type = "MESH"
        light = MagicMock()
        light.name = "Key"
        light.type = "LIGHT"

        child_col = MagicMock()
        child_col.name = "Props"
        child_col.objects = [mesh]
        child_col.children = []
        root_col = MagicMock()
        root_col.name = "Scene Collection"
        root_col.objects = [light]
        root_col.children = [child_col]

        bpy = make_mock_bpy()
        bpy.context.scene.collection = root_col
        bpy.context.scene.objects = [mesh, light]

        result = load_and_call("blender-scene/scripts/get_scene_info.py", bpy)

        assert result["success"] is True
        ctx = result["context"]
        assert ctx["total_objects"] == 2
        assert ctx["objects_by_type"] == {"MESH": 1, "LIGHT": 1}
        assert ctx["collections"]["children"][0]["name"] == "Props"
        assert ctx["collections"]["children"][0]["objects"] == ["Cube"]


def test_public_scene_catalog_preserves_read_and_file_contracts(monkeypatch):
    """Exercise Core's real YAML loader, not only the adapter's source flags."""
    from dcc_mcp_core import create_skill_server

    from tests.conftest import SKILLS_ROOT

    monkeypatch.setenv("DCC_MCP_DISABLE_DEFAULT_SKILL_PATHS", "1")
    server = create_skill_server("blender", extra_paths=[str(SKILLS_ROOT)])
    executor = MagicMock(return_value="{}")
    server.set_in_process_executor(executor)
    server.load_skill("blender-scene")
    tools = {tool["name"]: tool for tool in server.get_skill_info("blender-scene")["tools"]}
    readers = {"get_session_info", "get_scene_info", "list_objects", "get_viewport_info"}
    for name in readers | {"new_scene", "save_scene", "open_scene", "close_scene", "set_viewport_view"}:
        tool = tools[name]
        assert tool["thread-affinity"] == "main"
        assert tool["enforce_thread_affinity"] is True
        hints = tool["annotations"]
        assert hints["read_only_hint"] is (name in readers)
        assert hints["destructive_hint"] is (name in {"new_scene", "open_scene", "close_scene"})
        assert hints["idempotent_hint"] is (name in readers or name in {"save_scene", "set_viewport_view"})
        assert hints["open_world_hint"] is (name in {"save_scene", "open_scene"})
        assert tool["read_only"] is hints["read_only_hint"]
        assert tool["destructive"] is hints["destructive_hint"]
        assert tool["idempotent"] is hints["idempotent_hint"]
        schema = tool["input_schema"]
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        if name not in {"save_scene", "open_scene", "list_objects", "set_viewport_view"}:
            assert schema["properties"] == {}
    navigation = tools["set_viewport_view"]["input_schema"]
    assert navigation["required"] == ["area_index"]
    assert navigation["properties"]["axis"]["enum"] == ["FRONT", "BACK", "LEFT", "RIGHT", "TOP", "BOTTOM"]
    assert tools["open_scene"]["input_schema"]["required"] == ["filepath"]
    assert tools["open_scene"]["input_schema"]["properties"]["filepath"]["type"] == "string"
    save = tools["save_scene"]["input_schema"]
    assert not save.get("required")
    assert save["properties"]["filepath"]["type"] == ["string", "null"]
    listing = tools["list_objects"]["input_schema"]
    assert not listing.get("required")
    assert listing["properties"]["limit"]["default"] == 20
    assert listing["properties"]["limit"]["maximum"] == 100
    assert listing["properties"]["offset"]["minimum"] == 0
    assert listing["properties"]["name_filter"]["type"] == "string"
    assert listing["properties"]["object_type"]["type"] == ["string", "null"]
    executor.assert_not_called()


def test_object_listing_pages_and_combines_filters():
    objects = []
    for index in range(45):
        obj = MagicMock()
        obj.name = f"Target[{index:02}]"
        obj.type = "MESH" if index < 30 else "EMPTY"
        obj.location = [0, 0, 0]
        obj.hide_viewport = False
        objects.append(obj)
    bpy = make_mock_bpy()
    bpy.data.objects = objects
    first = load_and_call("blender-scene/scripts/list_objects.py", bpy)["context"]
    assert first["count"] == first["total"] == 45
    assert first["returned"] == len(first["objects"]) == 20
    assert first["next_offset"] == 20
    second = load_and_call("blender-scene/scripts/list_objects.py", bpy, offset=20, limit=20)["context"]
    assert second["objects"][0]["name"] == "Target[20]"
    assert second["next_offset"] == 40
    last = load_and_call("blender-scene/scripts/list_objects.py", bpy, offset=40)["context"]
    assert last["returned"] == 5
    assert last["next_offset"] is None
    filtered = load_and_call(
        "blender-scene/scripts/list_objects.py", bpy, object_type="mesh", name_filter="[2", limit=3
    )["context"]
    assert filtered["total"] == 10
    assert filtered["returned"] == 3
    assert filtered["next_offset"] == 3
    assert filtered["objects"][0]["name"] == "Target[20]"
    empty = load_and_call("blender-scene/scripts/list_objects.py", bpy, offset=99)["context"]
    assert empty["objects"] == []
    assert empty["total"] == 45
    assert empty["next_offset"] is None


def test_object_listing_rejects_invalid_page_arguments():
    for arguments in ({"limit": 0}, {"limit": 101}, {"limit": True}, {"limit": 1.5}, {"offset": -1}):
        result = load_and_call("blender-scene/scripts/list_objects.py", make_mock_bpy(), **arguments)
        assert result["success"] is False
        assert "invalid_pagination" in str(result)
