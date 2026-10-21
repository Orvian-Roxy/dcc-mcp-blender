"""Save the current Blender scene with shared native and managed readback."""

from __future__ import annotations

from typing import Optional

from dcc_mcp_core.skill import skill_entry


def save_scene(filepath: Optional[str] = None) -> dict:
    """Use the shared save confirmation without retrying uncertain writes."""
    from dcc_mcp_blender._scene_files import save_scene as guarded_save

    return guarded_save(filepath)


@skill_entry
def main(**kwargs) -> dict:
    """Entry point; delegates to :func:`save_scene`."""
    return save_scene(**kwargs)


if __name__ == "__main__":
    from dcc_mcp_core.skill import run_main

    run_main(main)
