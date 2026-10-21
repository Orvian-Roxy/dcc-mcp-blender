"""Capture the active viewport through the shared implementation."""

from dcc_mcp_core.skill import skill_entry

from dcc_mcp_blender._viewport_capture import capture_viewport


@skill_entry
def main(**kwargs) -> dict:
    return capture_viewport(**kwargs)


if __name__ == "__main__":
    from dcc_mcp_core.skill import run_main

    run_main(main)
