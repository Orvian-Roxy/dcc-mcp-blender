"""Capture a bounded batch of reversible node, property and material candidates."""

from dcc_mcp_core.skill import skill_entry

from dcc_mcp_blender._node_preview_ops import preview_variants


@skill_entry
def main(**kwargs) -> dict:
    return preview_variants(**kwargs)


if __name__ == "__main__":
    from dcc_mcp_core.skill import run_main

    run_main(main)
