"""Node schema on the selected Blender instance."""

from dcc_mcp_core.skill import skill_entry

from dcc_mcp_blender._node_runtime_ops import node_schema


@skill_entry
def main(**kwargs) -> dict:
    return node_schema(**kwargs)


if __name__ == "__main__":
    from dcc_mcp_core.skill import run_main

    run_main(main)
