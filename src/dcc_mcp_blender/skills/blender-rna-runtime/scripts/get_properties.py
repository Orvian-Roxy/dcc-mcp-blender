"""Inspect bounded RNA owner/PropertyGroup properties or read one scalar path, including plugin settings."""

from __future__ import annotations

from dcc_mcp_core.skill import run_main, skill_entry

from dcc_mcp_blender._rna_runtime_ops import get_properties


@skill_entry
def main(**kwargs) -> dict:
    """Execute on the Blender main thread through the skill dispatcher."""
    return get_properties(**kwargs)


if __name__ == "__main__":
    run_main(main)
