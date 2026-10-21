"""Fixed viewport observation must not create managed writes."""

from pathlib import Path
from types import SimpleNamespace as NS

import pytest
import yaml
from dcc_mcp_core import HostExecutionBridge

from dcc_mcp_blender import _managed_writes
from tests.conftest import load_and_call, make_mock_bpy


def navigation_scene():
    bpy = make_mock_bpy()
    region = NS(view_perspective="ORTHO", view_distance=4.0, view_location=(1, 2, 3), view_rotation=(1, 0, 0, 0))
    space = NS(
        region_3d=region,
        overlay=NS(show_overlays=True),
        shading=NS(type="RENDERED", use_scene_world=False, use_scene_lights=False, use_compositor="CAMERA"),
    )
    area = NS(
        type="VIEW_3D",
        width=900,
        height=600,
        spaces=NS(active=space),
        regions=[NS(type="WINDOW")],
        tag_redraw=lambda: None,
    )
    bpy.context.window_manager.windows = [NS(screen=NS(name="Layout", areas=[NS(type="CONSOLE"), area]))]
    return bpy, region


def test_typed_navigation_changes_only_the_selected_view_and_returns_readback():
    bpy, region = navigation_scene()
    bpy.ops.view3d.view_axis.return_value = {"FINISHED"}
    result = load_and_call(
        "blender-scene/scripts/set_viewport_view.py",
        bpy,
        area_index=1,
        axis="FRONT",
        view_location=[0, 0, 0.8],
        view_distance=0.48,
    )
    assert result["success"]
    bpy.ops.view3d.view_axis.assert_called_once_with(type="FRONT")
    assert list(region.view_location) == [0, 0, 0.8]
    assert region.view_distance == 0.48
    assert result["context"]["view_location"] == [0, 0, 0.8]
    assert bpy.data.is_dirty is False
    bpy.ops.wm.save_mainfile.assert_not_called()


@pytest.mark.parametrize(
    "arguments",
    [
        {"area_index": 0, "axis": "FRONT"},
        {"area_index": 1, "view_distance": -1},
        {"area_index": 1, "view_rotation": [0, 0, 0, 0]},
        {"area_index": 1, "view_location": [0, float("inf"), 0]},
    ],
)
def test_navigation_rejects_invalid_target_or_transform_before_changes(arguments):
    bpy, region = navigation_scene()
    result = load_and_call("blender-scene/scripts/set_viewport_view.py", bpy, **arguments)
    assert not result["success"]
    assert region.view_distance == 4.0
    bpy.ops.view3d.view_axis.assert_not_called()


@pytest.mark.parametrize("already_pending", [False, True])
def test_typed_navigation_does_not_create_or_clear_scene_write_state(already_pending):
    bpy, _ = navigation_scene()
    manifest = Path(__file__).parents[1] / "src/dcc_mcp_blender/skills/blender-scene/tools.yaml"
    tools = [NS(**tool) for tool in yaml.safe_load(manifest.read_text())["tools"]]
    bridge = HostExecutionBridge()
    _managed_writes.install_write_guard(bridge, lambda name: NS(tools=tools))
    if already_pending:
        bridge.dispatch_callable(lambda: None, action_name="edit", skill_name="test")
    before = _managed_writes.current_revision()
    pending = _managed_writes.pending_write()
    result = bridge.dispatch_callable(
        lambda: load_and_call("blender-scene/scripts/set_viewport_view.py", bpy, area_index=1, view_distance=0.48),
        action_name="blender_scene__set_viewport_view",
        skill_name="blender-scene",
    )
    assert result["success"]
    assert _managed_writes.current_revision() == before
    assert _managed_writes.pending_write() is pending


def test_navigation_leaves_camera_view_before_calling_axis_operator():
    bpy, region = navigation_scene()
    region.view_perspective = "CAMERA"

    def axis(**kwargs):
        assert region.view_perspective == "ORTHO"
        return {"FINISHED"}

    bpy.ops.view3d.view_axis.side_effect = axis
    result = load_and_call("blender-scene/scripts/set_viewport_view.py", bpy, area_index=1, axis="FRONT")
    assert result["success"]
    region.view_perspective = "CAMERA"
    refused = load_and_call("blender-scene/scripts/set_viewport_view.py", bpy, area_index=1, view_distance=0.5)
    assert not refused["success"]


def test_explicit_perspective_is_applied_after_native_axis_selection():
    bpy, region = navigation_scene()

    def axis(**kwargs):
        region.view_perspective = "ORTHO"
        return {"FINISHED"}

    bpy.ops.view3d.view_axis.side_effect = axis
    result = load_and_call(
        "blender-scene/scripts/set_viewport_view.py", bpy, area_index=1, axis="FRONT", view_perspective="PERSP"
    )
    assert result["success"]
    assert result["context"]["view_perspective"] == "PERSP"


def test_display_only_change_keeps_camera_navigation_and_existing_edits():
    bpy, region = navigation_scene()
    region.view_perspective = "CAMERA"
    bpy.data.is_dirty = True
    result = load_and_call(
        "blender-scene/scripts/set_viewport_view.py",
        bpy,
        area_index=1,
        shading_type="RENDERED",
        use_compositor="ALWAYS",
        show_overlays=False,
    )
    assert result["success"]
    assert region.view_perspective == "CAMERA"
    assert region.view_distance == 4.0
    assert list(region.view_location) == [1, 2, 3]
    assert result["context"]["shading"] == {"type": "RENDERED", "use_compositor": "ALWAYS"}
    assert result["context"]["show_overlays"] is False
    assert bpy.data.is_dirty is True
    bpy.ops.view3d.view_axis.assert_not_called()


def test_invalid_display_option_does_not_partially_navigate():
    bpy, region = navigation_scene()
    result = load_and_call(
        "blender-scene/scripts/set_viewport_view.py", bpy, area_index=1, axis="FRONT", use_compositor="INVALID"
    )
    assert not result["success"]
    assert region.view_distance == 4.0
    bpy.ops.view3d.view_axis.assert_not_called()


def test_viewports_report_active_spaces_without_operators():
    region = NS(view_perspective="ORTHO", view_distance=4.0, view_location=(1, 2, 3), view_rotation=(1, 0, 0, 0))
    space = NS(shading=NS(type="MATERIAL", use_scene_world=True, use_scene_lights=False), region_3d=region)
    area = NS(type="VIEW_3D", width=900, height=600, spaces=NS(active=space))
    bpy = make_mock_bpy()
    bpy.context.window_manager.windows = [NS(screen=NS(name="Layout", areas=[NS(type="CONSOLE"), area]))]
    result = load_and_call("blender-scene/scripts/get_viewport_info.py", bpy)
    assert result["success"]
    row = result["context"]["viewports"][0]
    assert row["area_index"] == 1
    assert row["shading"] == {"type": "MATERIAL", "use_scene_world": True, "use_scene_lights": False}
    assert row["view_perspective"] == "ORTHO"
    assert row["view_rotation"] == [1, 0, 0, 0]
    assert bpy.ops.mock_calls == []
    assert bpy.data.is_dirty is False


@pytest.mark.parametrize("already_pending", [False, True])
@pytest.mark.parametrize("fail", [False, True])
def test_no_gui_is_empty_and_actual_metadata_preserves_revision(already_pending, fail):
    bpy = make_mock_bpy()
    bpy.context.window_manager.windows = [NS()] if fail else []
    manifest = Path(__file__).parents[1] / "src/dcc_mcp_blender/skills/blender-scene/tools.yaml"
    tools = [NS(**tool) for tool in yaml.safe_load(manifest.read_text())["tools"]]
    bridge = HostExecutionBridge()
    _managed_writes.install_write_guard(bridge, lambda name: NS(tools=tools))
    if already_pending:
        bridge.dispatch_callable(lambda: None, action_name="unknown-write", skill_name="test")
    before = _managed_writes.current_revision()
    pending = _managed_writes.pending_write()
    result = bridge.dispatch_callable(
        lambda: load_and_call("blender-scene/scripts/get_viewport_info.py", bpy),
        action_name="blender_scene__get_viewport_info",
        skill_name="blender-scene",
    )
    assert result["success"] is not fail
    if not fail:
        assert result["context"]["viewports"] == []
    assert _managed_writes.current_revision() == before
    assert _managed_writes.pending_write() == pending
