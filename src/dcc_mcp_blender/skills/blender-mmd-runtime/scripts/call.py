"""Call the existing native MMD dispatch in this Blender process."""

from dcc_mcp_core.skill import run_main, skill_entry

from dcc_mcp_blender._native_runtime_ops import call


@skill_entry
def main(method: str, parameters: dict | None = None) -> dict:
    return call(method, parameters)


if __name__ == "__main__":
    run_main(main)
