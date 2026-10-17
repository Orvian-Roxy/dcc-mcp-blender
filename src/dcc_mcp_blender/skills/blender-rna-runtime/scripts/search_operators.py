"""Search live Operator identifiers and labels without precomputed plugin catalogs."""

from __future__ import annotations

from dcc_mcp_core.skill import run_main, skill_entry

from dcc_mcp_blender._rna_runtime_ops import search_operators


@skill_entry
def main(**kwargs) -> dict:
    """Execute on the Blender main thread through the skill dispatcher."""
    return search_operators(**kwargs)


if __name__ == "__main__":
    run_main(main)
