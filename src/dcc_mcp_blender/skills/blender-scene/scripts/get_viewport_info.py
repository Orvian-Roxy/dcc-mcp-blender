"""Observe existing GUI viewports without changing context or evaluating geometry."""

from __future__ import annotations

from dcc_mcp_core.skill import skill_entry, skill_error, skill_exception, skill_success


def get_viewport_info() -> dict:
    """Return active VIEW_3D spaces across open windows; headless returns an empty list."""
    try:
        import bpy

        viewports = []
        for window_index, window in enumerate(bpy.context.window_manager.windows):
            for area_index, area in enumerate(window.screen.areas):
                if area.type != "VIEW_3D":
                    continue
                space = area.spaces.active
                region = space.region_3d
                viewports.append(
                    {
                        "window_index": window_index,
                        "screen": window.screen.name,
                        "area_index": area_index,
                        "width": area.width,
                        "height": area.height,
                        "shading": {
                            "type": space.shading.type,
                            "use_scene_world": space.shading.use_scene_world,
                            "use_scene_lights": space.shading.use_scene_lights,
                        },
                        "view_perspective": region.view_perspective if region else None,
                        "view_distance": region.view_distance if region else None,
                        "view_location": list(region.view_location) if region else None,
                        "view_rotation": list(region.view_rotation) if region else None,
                    }
                )
        return skill_success("Viewport state observed", viewports=viewports, count=len(viewports))
    except ImportError:
        return skill_error("Blender not available", "bpy could not be imported")
    except Exception as exc:
        return skill_exception(exc, message="Failed to inspect viewports")


@skill_entry
def main(**kwargs) -> dict:
    """Delegate to the fixed viewport observation."""
    return get_viewport_info(**kwargs)


if __name__ == "__main__":
    from dcc_mcp_core.skill import run_main

    run_main(main)
