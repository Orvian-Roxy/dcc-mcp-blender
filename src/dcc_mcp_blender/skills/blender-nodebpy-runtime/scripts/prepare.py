"""Run the NodeBPY prepare entry on Blender's main thread."""

from dcc_mcp_core.skill import run_main, skill_entry

from dcc_mcp_blender._nodebpy_runtime_ops import prepare


@skill_entry
def main(**kwargs) -> dict:
    return prepare(**kwargs)


if __name__ == "__main__":
    run_main(main)
