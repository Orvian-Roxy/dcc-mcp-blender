"""Save the current Blender file."""

from __future__ import annotations

from dcc_mcp_core.skill import skill_entry


def save_blend(path: str) -> dict:
    """Save the current file using ``bpy.ops.wm.save_as_mainfile``."""
    from dcc_mcp_blender._scene_files import save_scene

    return save_scene(path)


@skill_entry
def main(**kwargs) -> dict:
    """Entry point; delegates to :func:`save_blend`."""
    return save_blend(**kwargs)


if __name__ == "__main__":
    from dcc_mcp_core.skill import run_main

    run_main(main)
