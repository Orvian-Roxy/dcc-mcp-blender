"""Describe actual live Operator parameter types, enums and ranges."""

from __future__ import annotations

from dcc_mcp_core.skill import run_main, skill_entry

from dcc_mcp_blender._rna_runtime_ops import describe_operator


@skill_entry
def main(**kwargs) -> dict:
    """Execute on the Blender main thread through the skill dispatcher."""
    return describe_operator(**kwargs)


if __name__ == "__main__":
    run_main(main)
