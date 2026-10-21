"""Navigate one existing GUI viewport without authoring scene data."""

from __future__ import annotations

import math

from dcc_mcp_core.skill import run_main, skill_entry, skill_error, skill_exception, skill_success


def _vector(value, length):
    if not isinstance(value, (list, tuple)) or len(value) != length:
        raise ValueError(f"Expected {length} finite numbers")
    if any(type(number) not in (int, float) or not math.isfinite(number) for number in value):
        raise ValueError("View transforms must be finite numbers")
    return list(value)


def set_viewport_view(
    area_index,
    window_index=0,
    axis=None,
    view_location=None,
    view_rotation=None,
    view_distance=None,
    view_perspective=None,
    shading_type=None,
    use_compositor=None,
    show_overlays=None,
):
    """Use window/area identifiers from get_viewport_info; never select objects or save."""
    try:
        import bpy

        if any(type(index) is not int or index < 0 for index in (window_index, area_index)):
            raise ValueError("Window and area indices must be nonnegative integers")
        if axis is not None and axis not in {"FRONT", "BACK", "LEFT", "RIGHT", "TOP", "BOTTOM"}:
            raise ValueError("Unknown view axis")
        if axis is not None and view_rotation is not None:
            raise ValueError("Choose axis or view_rotation")
        location = _vector(view_location, 3) if view_location is not None else None
        rotation = _vector(view_rotation, 4) if view_rotation is not None else None
        if rotation is not None:
            norm = math.hypot(*rotation)
            if not norm or not math.isfinite(norm):
                raise ValueError("View rotation must be a nonzero quaternion")
            rotation = [number / norm for number in rotation]
        if view_distance is not None and (
            type(view_distance) not in (int, float) or not math.isfinite(view_distance) or view_distance <= 0
        ):
            raise ValueError("View distance must be finite and positive")
        if view_perspective is not None and view_perspective not in {"ORTHO", "PERSP"}:
            raise ValueError("View perspective must be ORTHO or PERSP")
        if shading_type is not None and shading_type not in {"WIREFRAME", "SOLID", "MATERIAL", "RENDERED"}:
            raise ValueError("Unknown viewport shading type")
        if use_compositor is not None and use_compositor not in {"DISABLED", "CAMERA", "ALWAYS"}:
            raise ValueError("Unknown viewport compositor mode")
        if show_overlays is not None and type(show_overlays) is not bool:
            raise ValueError("show_overlays must be boolean")
        navigation = any(value is not None for value in (axis, location, rotation, view_distance, view_perspective))
        if not navigation and all(value is None for value in (shading_type, use_compositor, show_overlays)):
            raise ValueError("Provide a view transform or display setting")
        window = bpy.context.window_manager.windows[window_index]
        area = window.screen.areas[area_index]
        if area.type != "VIEW_3D" or area.spaces.active.region_3d is None:
            raise ValueError("Target area is not a 3D viewport")
        space = area.spaces.active
        region = space.region_3d
        ui_region = next(item for item in area.regions if item.type == "WINDOW")
        display = [
            (space.shading, "type", shading_type),
            (space.shading, "use_compositor", use_compositor),
            (space.overlay, "show_overlays", show_overlays),
        ]
        if any(value is not None and not hasattr(owner, key) for owner, key, value in display):
            raise ValueError("Viewport does not support the requested display setting")
        if navigation and region.view_perspective == "CAMERA" and axis is None and view_perspective is None:
            raise ValueError("Choose an axis or non-camera perspective before navigating a camera view")
        # Leave camera view before navigation so a locked camera is never moved.
        if view_perspective is not None:
            region.view_perspective = view_perspective
        elif axis is not None:
            region.view_perspective = "ORTHO"
        if axis is not None:
            with bpy.context.temp_override(window=window, area=area, region=ui_region):
                status = bpy.ops.view3d.view_axis(type=axis)
            if "FINISHED" not in status:
                return skill_error("View axis was not applied", "view_axis_incomplete", operator_status=sorted(status))
        if view_perspective is not None:
            # Native axis selection may force ORTHO via the user's auto-perspective setting.
            region.view_perspective = view_perspective
        if rotation is not None:
            region.view_rotation = rotation
        if location is not None:
            region.view_location = location
        if view_distance is not None:
            region.view_distance = view_distance
        for owner, key, value in display:
            if value is not None:
                setattr(owner, key, value)
        area.tag_redraw()
        return skill_success(
            "Viewport navigated",
            window_index=window_index,
            area_index=area_index,
            view_perspective=region.view_perspective,
            view_distance=region.view_distance,
            view_location=list(region.view_location),
            view_rotation=list(region.view_rotation),
            shading={"type": space.shading.type, "use_compositor": getattr(space.shading, "use_compositor", None)},
            show_overlays=space.overlay.show_overlays,
        )
    except Exception as exc:
        return skill_exception(exc, message="Viewport navigation failed")


@skill_entry
def main(**kwargs):
    return set_viewport_view(**kwargs)


if __name__ == "__main__":
    run_main(main)
