"""Discover the optional native MMD methods."""

from dcc_mcp_core.skill import run_main, skill_entry

from dcc_mcp_blender._native_runtime_ops import list_methods


@skill_entry
def main() -> dict:
    return list_methods()


if __name__ == "__main__":
    run_main(main)
