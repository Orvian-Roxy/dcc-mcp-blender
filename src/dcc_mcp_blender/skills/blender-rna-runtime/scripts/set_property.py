"""Write one RNA scalar or fixed array and return actual readback. Use Native providers for coupled selection changes."""

from __future__ import annotations

from dcc_mcp_core.skill import run_main, skill_entry

from dcc_mcp_blender._rna_runtime_ops import set_property


@skill_entry
def main(**kwargs) -> dict:
    """Execute on the Blender main thread through the skill dispatcher."""
    return set_property(**kwargs)


if __name__ == "__main__":
    run_main(main)
