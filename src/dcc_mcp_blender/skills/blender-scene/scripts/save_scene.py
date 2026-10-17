"""Save the current Blender scene."""

from __future__ import annotations

from typing import Optional

from dcc_mcp_core.skill import skill_entry


def save_scene(filepath: Optional[str] = None) -> dict:
    """Save the current Blender scene.

    Args:
        filepath: Optional path to save the scene to. If None, saves to the
            current file path. If the scene has never been saved, this is required.

    Returns:
        ActionResultModel dict.
    """
    from dcc_mcp_blender._scene_files import save_scene as confirmed_save

    return confirmed_save(filepath)


@skill_entry
def main(**kwargs) -> dict:
    """Entry point; delegates to :func:`save_scene`."""
    return save_scene(**kwargs)


if __name__ == "__main__":
    from dcc_mcp_core.skill import run_main

    run_main(main)
