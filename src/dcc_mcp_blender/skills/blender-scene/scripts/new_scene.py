"""Create a new empty Blender scene.

After ``read_factory_settings`` Blender wipes all registered timers — including
the MCP main-thread dispatcher pump.  This module restores the pump so subsequent
tool calls succeed without restarting Blender.
"""

from __future__ import annotations

from dcc_mcp_core.skill import skill_entry


def new_scene() -> dict:
    """Create a new Blender scene by loading the default startup file.

    Returns:
        ActionResultModel dict.
    """
    from dcc_mcp_blender._scene_files import new_scene as guarded_new

    return guarded_new()


@skill_entry
def main(**kwargs) -> dict:
    """Entry point; delegates to :func:`new_scene`."""
    return new_scene(**kwargs)


if __name__ == "__main__":
    from dcc_mcp_core.skill import run_main

    run_main(main)
