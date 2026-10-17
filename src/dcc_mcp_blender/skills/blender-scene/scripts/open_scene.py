"""Open a Blender scene file from disk."""

from __future__ import annotations

from dcc_mcp_core.skill import skill_entry


def open_scene(filepath: str) -> dict:
    """Open a Blender .blend file from disk.

    Args:
        filepath: Absolute path to the .blend file to open.

    Returns:
        ActionResultModel dict.
    """
    from dcc_mcp_blender._scene_files import open_scene as guarded_open

    return guarded_open(filepath)


@skill_entry
def main(**kwargs) -> dict:
    """Entry point; delegates to :func:`open_scene`."""
    return open_scene(**kwargs)


if __name__ == "__main__":
    from dcc_mcp_core.skill import run_main

    run_main(main)
