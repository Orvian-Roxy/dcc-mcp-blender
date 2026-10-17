"""Launch an isolated NPR using the official DCC CLI and explicit dependencies.

Requires Python 3.11+ (the prepared environment uses 3.13).

Examples:
  python tools/npr_runtime.py start --config /absolute/npr.json --dry-run
  python tools/npr_runtime.py start --config /absolute/npr.json
  python tools/npr_runtime.py status --work-dir /absolute/private-run

Config requires binary_path, binary_sha256, expected_build, target_npr, cli_path,
gateway_bin, gateway_port, source_root, site_packages, project_root and work_dir.
python_paths accepts existing project/addon directories and .whl files (zipimport).
No packages are installed or copied; current production configuration is not used.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import os
import re
import socket
import subprocess
import sys
import time
import traceback
from pathlib import Path

import tomllib

VERSIONS = {"adapter": "0.2.21", "core": "0.20.45", "server": "0.20.45", "cli": "0.20.45"}


def validated(config):
    config = dict(config)
    for name in ("binary_path", "cli_path", "gateway_bin", "source_root", "site_packages", "project_root", "work_dir"):
        value = config.get(name)
        if not isinstance(value, str) or not Path(value).expanduser().is_absolute():
            raise ValueError(f"Provide an absolute {name} in --config.")
        path = Path(value).expanduser().resolve()
        if name != "work_dir" and not path.exists():
            raise ValueError(f"{name} does not exist: {path}; fix --config before launching.")
        config[name] = str(path)
    for name in ("binary_path", "cli_path", "gateway_bin"):
        if not Path(config[name]).is_file() or not os.access(config[name], os.X_OK):
            raise ValueError(f"{name} must name an existing executable file.")
    for name in ("source_root", "site_packages", "project_root"):
        if not Path(config[name]).is_dir():
            raise ValueError(f"{name} must name an existing directory.")
    source = Path(config["source_root"])
    if any(
        not (source / "src/dcc_mcp_blender" / name).is_file() for name in ("__init__.py", "server.py", "_public_api.py")
    ):
        raise ValueError("source_root must contain this DCC adapter checkout and server entry points.")
    if tomllib.loads((source / "pyproject.toml").read_text())["project"]["version"] != VERSIONS["adapter"]:
        raise ValueError("source_root is not the selected adapter version.")
    site = Path(config["site_packages"])
    installed = {
        (dist.metadata.get("Name") or "").lower().replace("_", "-"): dist.version
        for dist in importlib.metadata.Distribution.discover(path=[str(site)])
    }
    for name in ("core", "server"):
        package = site / f"dcc_mcp_{name}"
        if installed.get(f"dcc-mcp-{name}") != VERSIONS[name] or not (package / "__init__.py").is_file():
            raise ValueError(
                f"site_packages must contain installed dcc-mcp-{name} {VERSIONS[name]}; use an existing prepared environment."
            )
    if not any(path.suffix in {".so", ".pyd"} for path in (site / "dcc_mcp_core").glob("_core*")):
        raise ValueError("site_packages is missing the native Core extension required by this NPR.")
    if not re.fullmatch(r"[a-fA-F0-9]{64}", str(config.get("binary_sha256", ""))):
        raise ValueError("Provide the selected NPR binary_sha256 (64 hex characters).")
    if not re.fullmatch(r"[a-fA-F0-9]{40}", str(config.get("expected_build", ""))):
        raise ValueError("Provide the selected NPR expected_build (40 hex characters).")
    with Path(config["binary_path"]).open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != config["binary_sha256"].lower():
        raise ValueError("NPR binary SHA-256 differs from --config; use the selected fixed binary.")
    config["binary_sha256"] = actual
    config["expected_build"] = config["expected_build"].lower()
    if not isinstance(config.get("target_npr"), str) or not config["target_npr"].strip():
        raise ValueError("Provide target_npr, for example 0.4.0-alpha.4.")
    port = config.get("gateway_port")
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError("gateway_port must be an explicit private port between 1024 and 65535.")
    paths = config.get("python_paths", [])
    if not isinstance(paths, list) or any(
        not isinstance(path, str)
        or not Path(path).is_absolute()
        or not (Path(path).is_dir() or (Path(path).is_file() and Path(path).suffix == ".whl"))
        for path in paths
    ):
        raise ValueError("python_paths must contain existing absolute directories or .whl files.")
    config["python_paths"] = [str(Path(path).resolve()) for path in paths]
    interval = config.get("idle_interval_secs", 0.5)
    if (
        isinstance(interval, bool)
        or not isinstance(interval, (float, int))
        or not math.isfinite(interval)
        or interval <= 0
    ):
        raise ValueError("idle_interval_secs must be a finite positive number; baseline is 0.5.")
    config["idle_interval_secs"] = interval
    config["base_url"] = f"http://127.0.0.1:{port}"
    return config


def launch_plan(config):
    return dict(
        schema_version=1,
        dcc_type="blender",
        executable=config["binary_path"],
        version=config["target_npr"],
        argv=[
            config["binary_path"],
            "--factory-startup",
            "--disable-autoexec",
            "--python",
            str(Path(__file__).resolve()),
            "--",
            "bootstrap",
            "--config",
            str(Path(config["work_dir"]) / "config.json"),
        ],
        cwd=config["project_root"],
    )


def environment(config):
    work = Path(config["work_dir"])
    return {
        **{
            f"BLENDER_USER_{key}": str(work / directory)
            for key, directory in (
                ("CONFIG", "config"),
                ("SCRIPTS", "scripts"),
                ("DATAFILES", "datafiles"),
                ("EXTENSIONS", "extensions"),
            )
        },
        "DCC_MCP_REGISTRY_DIR": str(work / "registry"),
        "DCC_MCP_GATEWAY_PROFILE": "local",
        "DCC_MCP_GATEWAY_PORT": str(config["gateway_port"]),
        "DCC_MCP_GATEWAY_REMOTE_PORT": "0",
        "NODEBPY_BLENDER_BINARY": config["binary_path"],
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def start(config, dry_run=False):
    config = validated(config)
    work = Path(config["work_dir"])
    if work.exists():
        raise ValueError(
            f"Private directory already exists; observe it with status --work-dir {work}. Use a new directory to launch."
        )
    if dry_run:
        return dict(
            ready=False,
            launch_plan=launch_plan(config),
            environment=environment(config),
            idle_interval_secs=config["idle_interval_secs"],
        )
    for key, name in (("cli_path", "dcc-mcp-cli"), ("gateway_bin", "dcc-mcp-server")):
        version = subprocess.run([config[key], "--version"], capture_output=True, text=True, timeout=10, check=False)
        if version.returncode or version.stdout.strip().split() != [name, VERSIONS["cli"]]:
            raise ValueError(f"{key} must be {name} {VERSIONS['cli']}; fix --config.")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", config["gateway_port"]))
    work.mkdir(parents=True, exist_ok=False)
    for directory in ("config", "scripts", "datafiles", "extensions", "registry"):
        (work / directory).mkdir()
    (work / "config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2))
    (work / "launch-plan.json").write_text(json.dumps(launch_plan(config), ensure_ascii=False, indent=2))
    child_env = {**os.environ, **environment(config)}
    cli_run(
        config,
        [
            "gateway",
            "ensure",
            "--host",
            "127.0.0.1",
            "--port",
            str(config["gateway_port"]),
            "--remote-port",
            "0",
            "--registry-dir",
            str(work / "registry"),
            "--gateway-bin",
            config["gateway_bin"],
        ],
        child_env,
        "gateway.json",
    )
    report = cli_run(
        config,
        [
            "start-instance",
            "--dcc-type",
            "blender",
            "--project",
            config["project_root"],
            "--launch-plan",
            str(work / "launch-plan.json"),
            "--version",
            config["target_npr"],
            "--wait-ready",
            "--timeout-secs",
            "90",
            "--yes",
        ],
        child_env,
        "launch.json",
    )
    if report.get("ready") is not True:
        raise RuntimeError(f"NPR readiness is unconfirmed. Observe status --work-dir {work}; do not launch again.")
    if not (work / "host.json").is_file():
        raise RuntimeError(
            f"CLI registered a host but bootstrap identity is missing; inspect {work}/bootstrap-error.txt and use status, not start."
        )
    host = json.loads((work / "host.json").read_text())
    if host.get("instance_id") != report.get("instance_id"):
        raise RuntimeError(f"Host identity differs from the ready instance; inspect {work} without relaunching.")
    if (
        host.get("binary_path") != config["binary_path"]
        or host.get("binary_sha256") != config["binary_sha256"]
        or not config["expected_build"].startswith(str(host.get("build", "missing")))
        or len(str(host.get("build", ""))) < 12
        or host.get("background") is not False
        or any(host.get(key) != VERSIONS[key] for key in ("adapter", "core", "server"))
    ):
        raise RuntimeError(f"NPR identity does not match the selected environment; inspect {work} without relaunching.")
    return dict(
        ready=True,
        **host,
        work_dir=str(work),
        base_url=config["base_url"],
        cli_path=config["cli_path"],
        cli=VERSIONS["cli"],
        cold_ready_seconds=report.get("elapsed_ms", 0) / 1000,
    )


def cli_run(config, arguments, child_env, receipt=None, allow_failure=False):
    command = [
        config["cli_path"],
        "--base-url",
        config["base_url"],
        "--no-auto-gateway",
        "--non-interactive",
        "--output",
        "json",
        *arguments,
    ]
    completed = subprocess.run(command, env=child_env, capture_output=True, text=True, timeout=110, check=False)
    try:
        result = json.loads(completed.stdout)
    except ValueError:
        result = {"error": completed.stderr[-2000:], "stdout": completed.stdout[-2000:]}
    if receipt:
        (Path(config["work_dir"]) / receipt).write_text(json.dumps(result, ensure_ascii=False, indent=2))
    if completed.returncode and not allow_failure:
        raise RuntimeError(
            f"DCC CLI failed: {json.dumps(result)[:2000]}; inspect status --work-dir {config['work_dir']} before another launch."
        )
    return result


def bootstrap(config):
    """Run only in the selected Blender process; CLI owns launch and readiness."""
    work = Path(config["work_dir"])
    started = time.perf_counter()
    try:
        config = validated(config)
        sys.path[:0] = [str(Path(config["source_root"]) / "src"), config["site_packages"], *config["python_paths"]]
        import bpy
        import dcc_mcp_core

        import dcc_mcp_blender
        from dcc_mcp_blender.host import BlenderUiDispatcher

        binary = Path(bpy.app.binary_path).resolve()
        build = bpy.app.build_hash.decode()
        if (
            binary != Path(config["binary_path"])
            or not config["expected_build"].startswith(build)
            or len(build) < 12
            or bpy.app.background
        ):
            raise RuntimeError("Blender identity differs from the selected NPR GUI; inspect bootstrap-error.txt.")
        if not Path(dcc_mcp_blender.__file__).resolve().is_relative_to(Path(config["source_root"]) / "src"):
            raise RuntimeError(
                "Another adapter copy was loaded; use the explicit source_root and a fresh private profile."
            )
        versions = dict(
            adapter=dcc_mcp_blender.__version__,
            core=dcc_mcp_core.__version__,
            server=importlib.metadata.version("dcc-mcp-server"),
        )
        if (
            any(versions[key] != VERSIONS[key] for key in versions)
            or dcc_mcp_core._core.__version__ != VERSIONS["core"]
        ):
            raise RuntimeError(f"Loaded DCC versions differ from the fixed environment: {versions}")
        operation = os.environ.get("DCC_MCP_START_OPERATION_ID")
        project = os.environ.get("DCC_MCP_START_PROJECT")
        if not operation or not project or str(Path(project).resolve()) != config["project_root"]:
            raise RuntimeError("Launch through npr_runtime.py start so the official CLI supplies the project binding.")
        os.environ["NODEBPY_BLENDER_BINARY"] = str(binary)
        server = dcc_mcp_blender.start_server(
            port=0,
            gateway_port=config["gateway_port"],
            registry_dir=str(work / "registry"),
            dispatcher=BlenderUiDispatcher(idle_interval_secs=config["idle_interval_secs"]),
            include_bundled=False,
            enable_hot_reload=False,
            enable_gateway_failover=False,
            metrics_enabled=False,
            job_storage_path=str(work / "jobs.sqlite"),
            enable_workflows=False,
            gateway_remote_port=0,
        )
        # Core 0.20.45's public native method merges project binding into this adapter's row.
        server._handle.update_gateway_metadata({"dcc_mcp_project": project, "dcc_mcp_operation_id": operation})
        host = dict(
            instance_id=server.instance_id,
            instance_port=server.port,
            pid=os.getpid(),
            binary_path=str(binary),
            binary_sha256=config["binary_sha256"],
            build=build,
            blender=bpy.app.version_string,
            target_npr=config["target_npr"],
            background=bpy.app.background,
            **versions,
            idle_interval_secs=config["idle_interval_secs"],
            bootstrap_seconds=time.perf_counter() - started,
        )
        (work / "host.json").write_text(json.dumps(host, ensure_ascii=False, indent=2))
        return host
    except Exception:
        (work / "bootstrap-error.txt").write_text(traceback.format_exc())
        raise


def status(work_dir):
    """Observe a previous launch; never issue another launch or rewrite its files."""
    work = Path(work_dir).expanduser().resolve()
    config = json.loads((work / "config.json").read_text())
    host_path = work / "host.json"
    if not host_path.exists():
        error = work / "bootstrap-error.txt"
        return dict(
            ready=False,
            work_dir=str(work),
            error=error.read_text()[-2000:] if error.exists() else None,
            next_action="Inspect this directory's launch.json and bootstrap-error.txt; do not repeat start.",
        )
    host = json.loads(host_path.read_text())
    report = cli_run(
        config,
        ["wait-ready", "--dcc-type", "blender", "--instance-id", host["instance_id"], "--timeout-secs", "2"],
        {**os.environ, **environment(config)},
        allow_failure=True,
    )
    observed_id = report.get("instance", {}).get("instance_id")
    return dict(
        **host,
        ready=report.get("ready") is True and observed_id == host["instance_id"],
        work_dir=str(work),
        base_url=config["base_url"],
        observation=report,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    launch = commands.add_parser(
        "start",
        help="Start once in a new private directory.",
        epilog="Example: start --config /absolute/npr.json --dry-run",
    )
    launch.add_argument("--config", required=True, type=Path)
    launch.add_argument("--dry-run", action="store_true")
    internal = commands.add_parser("bootstrap", help="Internal: run by the launch plan inside Blender.")
    internal.add_argument("--config", required=True, type=Path)
    observe = commands.add_parser(
        "status",
        help="Observe the existing private launch without starting it again.",
        epilog="Example: status --work-dir /absolute/private-run",
    )
    observe.add_argument("--work-dir", required=True, type=Path)
    if argv is not None and not argv:
        parser.print_help()
        return 0
    args = parser.parse_args(argv)
    try:
        if args.command == "status":
            result = status(args.work_dir)
        else:
            config = json.loads(args.config.read_text())
            if not isinstance(config, dict):
                raise ValueError("--config must contain a JSON object; see --help for required fields.")
            result = bootstrap(config) if args.command == "bootstrap" else start(config, args.dry_run)
    except (ValueError, OSError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2
    except (RuntimeError, subprocess.TimeoutExpired) as exc:
        print(
            json.dumps({"error": str(exc), "next_action": "Observe the existing work directory; do not repeat start."}),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else sys.argv[1:]
    code = main(arguments)
    if not arguments or arguments[0] != "bootstrap":
        raise SystemExit(code)
