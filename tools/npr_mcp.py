"""Stdio startup entry for a configured NPR; scene operations stay on DCC MCP."""

import argparse
import asyncio
import json
import re
from pathlib import Path

import npr_runtime


class InstanceLauncher:
    def __init__(self, config_path):
        self.config_path = Path(config_path).expanduser().resolve()

    def work_dir(self, task_name):
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}", task_name):
            raise ValueError("Use a task name containing letters, digits, underscores or hyphens.")
        return self.config_path.parent / "instances" / task_name

    def start_instance(self, task_name, project_root):
        work = self.work_dir(task_name)
        config = json.loads(self.config_path.read_text())
        config.update(
            work_dir=str(work),
            project_root=str(Path(project_root).resolve()),
            gateway_port=9765,
            shared_gateway=True,
            registry_dir=str(self.config_path.parent.parent / "gateway" / "registry"),
        )
        return npr_runtime.start(config)

    def instance_status(self, task_name):
        return npr_runtime.status(self.work_dir(task_name))


def main():
    from mcp.server.mcpserver import MCPServer

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    service = InstanceLauncher(parser.parse_args().config)
    server = MCPServer("dcc-npr-startup")

    @server.tool()
    async def start_instance(task_name: str, project_root: str) -> dict:
        """Start one task-owned NPR using the configured binary and shared DCC gateway.

        Reuse the task name to observe an uncertain launch with instance_status;
        an existing work directory is never overwritten or launched again.
        Use the returned instance_id with the official DCC MCP for scene tools.
        """
        return await asyncio.to_thread(service.start_instance, task_name, project_root)

    @server.tool()
    async def instance_status(task_name: str) -> dict:
        """Observe this task's previous launch without starting or changing Blender."""
        return await asyncio.to_thread(service.instance_status, task_name)

    server.run(transport="stdio")


if __name__ == "__main__":
    main()
