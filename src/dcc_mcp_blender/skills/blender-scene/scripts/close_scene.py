"""Explicitly close Blender after preserving current edits."""

from __future__ import annotations

from dcc_mcp_core.skill import skill_entry

from dcc_mcp_blender._scene_files import close_scene


@skill_entry
def main(**kwargs) -> dict:
    """Request closure; a disconnected client must observe process state."""
    return close_scene(**kwargs)


if __name__ == "__main__":
    from dcc_mcp_core.skill import run_main

    run_main(main)
