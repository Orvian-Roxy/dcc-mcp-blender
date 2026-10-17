"""Run the NodeBPY project entry on Blender's main thread."""

from dcc_mcp_core.skill import run_main, skill_entry

from dcc_mcp_blender._nodebpy_runtime_ops import project


@skill_entry
def main(**kwargs) -> dict:
    return project(**kwargs)


if __name__ == "__main__":
    run_main(main)
