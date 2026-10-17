"""Public NPR launcher CLI checks; no GUI processes are created by these tests."""

import hashlib
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).parents[1] / "tools" / "npr_runtime.py"


def launcher():
    spec = importlib.util.spec_from_file_location("npr_runtime", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def config_file(tmp_path):
    binary = tmp_path / "NPR Blender"
    binary.write_bytes(b"test-owned executable")
    cli = tmp_path / "dcc-mcp-cli"
    cli.write_bytes(b"cli")
    gateway = tmp_path / "dcc-mcp-server"
    gateway.write_bytes(b"gateway")
    for executable in (binary, cli, gateway):
        executable.chmod(0o755)
    site = tmp_path / "site-packages"
    site.mkdir()
    for name in ("core", "server"):
        package = site / f"dcc_mcp_{name}"
        package.mkdir()
        (package / "__init__.py").write_text("")
        metadata = site / f"dcc_mcp_{name}-0.20.45.dist-info"
        metadata.mkdir()
        (metadata / "METADATA").write_text(f"Name: dcc-mcp-{name}\nVersion: 0.20.45\n")
    (site / "dcc_mcp_core" / "_core.test.so").write_bytes(b"native fixture")
    project = tmp_path / "project"
    project.mkdir()
    config = dict(
        binary_path=str(binary),
        binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
        expected_build="4b0f35d53dec951c212b2b42f8400679fda1b804",
        target_npr="0.4.0-alpha.4",
        cli_path=str(cli),
        gateway_bin=str(gateway),
        gateway_port=58231,
        source_root=str(SCRIPT.parents[1]),
        site_packages=str(site),
        project_root=str(project),
        work_dir=str(tmp_path / "isolated"),
        python_paths=[],
    )
    path = tmp_path / "input.json"
    path.write_text(json.dumps(config))
    return path, config


def test_dry_run_reports_exact_isolated_plan_without_starting_or_writing(tmp_path, capsys):
    config, data = config_file(tmp_path)
    with patch("subprocess.run") as process:
        result = launcher().main(["start", "--config", str(config), "--dry-run"])
    assert result == 0
    process.assert_not_called()
    output = json.loads(capsys.readouterr().out)
    assert output["ready"] is False
    assert output["launch_plan"]["executable"] == data["binary_path"]
    assert output["launch_plan"]["argv"][0] == data["binary_path"]
    assert output["environment"]["NODEBPY_BLENDER_BINARY"] == data["binary_path"]
    assert output["environment"]["BLENDER_USER_CONFIG"].startswith(data["work_dir"])
    assert output["idle_interval_secs"] == 0.5
    assert not Path(data["work_dir"]).exists()


def test_start_delegates_isolated_gateway_and_native_lifecycle_before_claiming_ready(tmp_path, capsys):
    from types import SimpleNamespace

    config, data = config_file(tmp_path)
    commands = []

    def cli(command, **kwargs):
        commands.append((command, kwargs))
        if len(command) == 2 and command[1] == "--version":
            return SimpleNamespace(
                returncode=0,
                stdout=("dcc-mcp-cli" if command[0] == data["cli_path"] else "dcc-mcp-server") + " 0.20.45\n",
                stderr="",
            )
        if "start-instance" in command:
            host = dict(
                instance_id="test-owned-instance",
                pid=1234,
                binary_path=data["binary_path"],
                binary_sha256=data["binary_sha256"],
                build=data["expected_build"][:12],
                adapter="0.2.21",
                core="0.20.45",
                server="0.20.45",
                background=False,
                instance_port=58235,
            )
            Path(data["work_dir"]).joinpath("host.json").write_text(json.dumps(host))
            result = dict(ok=True, ready=True, instance_id="test-owned-instance", elapsed_ms=1800)
        else:
            result = dict(ok=True, healthy=True)
        return SimpleNamespace(returncode=0, stdout=json.dumps(result), stderr="")

    with patch("subprocess.run", side_effect=cli):
        assert launcher().main(["start", "--config", str(config)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["ready"] is True
    assert output["instance_id"] == "test-owned-instance"
    assert output["binary_path"] == data["binary_path"]
    launch = next((cmd, kwargs) for cmd, kwargs in commands if "start-instance" in cmd)
    assert "--wait-ready" in launch[0] and "--yes" in launch[0]
    assert launch[1]["env"]["DCC_MCP_REGISTRY_DIR"] == str(Path(data["work_dir"]) / "registry")
    assert launch[1]["env"]["NODEBPY_BLENDER_BINARY"] == data["binary_path"]
    ensure = next(cmd for cmd, _ in commands if "ensure" in cmd)
    assert ensure[ensure.index("--gateway-bin") + 1] == data["gateway_bin"]
    assert ensure[ensure.index("--remote-port") + 1] == "0"
    assert Path(data["work_dir"], "launch.json").is_file()


def test_host_bootstrap_uses_exact_runtime_and_registers_native_lifecycle_binding(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace

    config, data = config_file(tmp_path)
    Path(data["work_dir"]).mkdir()
    wheel = tmp_path / "OpenCC.whl"
    wheel.write_bytes(b"test import path")
    data["python_paths"] = [str(wheel)]
    data["idle_interval_secs"] = 0.05
    config.write_text(json.dumps(data))
    calls = []
    server = SimpleNamespace(
        instance_id="host-instance",
        port=58235,
        _handle=SimpleNamespace(update_gateway_metadata=lambda values: calls.append(("metadata", values))),
    )
    adapter = SimpleNamespace(
        __file__=str(Path(data["source_root"]) / "src/dcc_mcp_blender/__init__.py"),
        __version__="0.2.21",
        start_server=lambda **kwargs: calls.append(("start", kwargs)) or server,
    )
    bpy = SimpleNamespace(
        app=SimpleNamespace(
            binary_path=data["binary_path"],
            build_hash=data["expected_build"][:12].encode(),
            background=False,
            version_string="5.2.0 LTS",
        )
    )
    monkeypatch.setenv("DCC_MCP_START_OPERATION_ID", "official-operation")
    monkeypatch.setenv("DCC_MCP_START_PROJECT", data["project_root"])
    with patch.dict(
        sys.modules,
        {
            "bpy": bpy,
            "dcc_mcp_blender": adapter,
            "dcc_mcp_blender.host": SimpleNamespace(BlenderUiDispatcher=lambda **kwargs: kwargs),
            "dcc_mcp_core": SimpleNamespace(__version__="0.20.45", _core=SimpleNamespace(__version__="0.20.45")),
        },
    ), patch.object(sys, "path", sys.path.copy()), patch.object(sys, "stdout"), patch.object(sys, "stderr"), patch(
        "importlib.metadata.version", return_value="0.20.45"
    ):
        assert launcher().main(["bootstrap", "--config", str(config)]) == 0
        assert str(wheel) in sys.path
    host = json.loads(Path(data["work_dir"], "host.json").read_text())
    assert host["instance_id"] == "host-instance"
    assert host["binary_path"] == data["binary_path"]
    assert host["core"] == "0.20.45"
    assert dict(calls)["start"]["dispatcher"]["idle_interval_secs"] == 0.05
    assert dict(calls)["metadata"] == {
        "dcc_mcp_project": data["project_root"],
        "dcc_mcp_operation_id": "official-operation",
    }


def test_status_observes_exact_previous_instance_without_starting(tmp_path, capsys):
    from types import SimpleNamespace

    _, data = config_file(tmp_path)
    work = Path(data["work_dir"])
    work.mkdir()
    data["base_url"] = f"http://127.0.0.1:{data['gateway_port']}"
    (work / "config.json").write_text(json.dumps(data))
    (work / "host.json").write_text(json.dumps({"instance_id": "observed-id", "pid": 123}))
    response = dict(ready=True, instance={"instance_id": "observed-id"})
    with patch(
        "subprocess.run", return_value=SimpleNamespace(returncode=0, stdout=json.dumps(response), stderr="")
    ) as call:
        assert launcher().main(["status", "--work-dir", str(work)]) == 0
    command = call.call_args.args[0]
    assert "wait-ready" in command
    assert command[command.index("--instance-id") + 1] == "observed-id"
    assert "start-instance" not in command and "ensure" not in command
    assert json.loads(capsys.readouterr().out)["instance_id"] == "observed-id"


def test_fixed_core_metadata_method_is_on_live_handle_not_inner_server():
    from dcc_mcp_core._core import McpHttpServer, McpServerHandle

    assert callable(McpServerHandle.update_gateway_metadata)
    assert not hasattr(McpHttpServer, "update_gateway_metadata")


def test_input_error_never_starts_and_existing_directory_is_preserved(tmp_path, capsys):
    config, data = config_file(tmp_path)
    work = Path(data["work_dir"])
    work.mkdir()
    sentinel = work / "existing.blend"
    sentinel.write_bytes(b"preserve me")
    with patch("subprocess.run") as process:
        assert launcher().main(["start", "--config", str(config)]) == 2
    process.assert_not_called()
    assert sentinel.read_bytes() == b"preserve me"
    assert "status --work-dir" in capsys.readouterr().err
    data["work_dir"] = str(tmp_path / "another-run")
    data["python_paths"] = [str(tmp_path / "missing.whl")]
    config.write_text(json.dumps(data))
    with patch("subprocess.run") as process:
        assert launcher().main(["start", "--config", str(config)]) == 2
    process.assert_not_called()
    assert not Path(data["work_dir"]).exists()


def test_unknown_launch_is_observed_without_relaunching_or_overwriting(tmp_path, capsys):
    from types import SimpleNamespace

    config, data = config_file(tmp_path)

    def unknown(command, **kwargs):
        if len(command) == 2:
            name = "dcc-mcp-cli" if command[0] == data["cli_path"] else "dcc-mcp-server"
            return SimpleNamespace(returncode=0, stdout=name + " 0.20.45\n", stderr="")
        return SimpleNamespace(returncode=0, stdout=json.dumps({"ready": False}), stderr="")

    with patch("subprocess.run", side_effect=unknown) as process:
        assert launcher().main(["start", "--config", str(config)]) == 1
        assert sum("start-instance" in call.args[0] for call in process.call_args_list) == 1
        process.reset_mock()
        assert launcher().main(["start", "--config", str(config)]) == 2
        process.assert_not_called()
        assert launcher().main(["status", "--work-dir", data["work_dir"]]) == 0
        process.assert_not_called()
    assert Path(data["work_dir"], "launch.json").read_text() == '{\n  "ready": false\n}'
    output = capsys.readouterr()
    assert json.loads(output.out)["ready"] is False


def test_cli_is_rejected_as_gateway_before_creating_private_directory(tmp_path, capsys):
    from types import SimpleNamespace

    config, data = config_file(tmp_path)
    with patch(
        "subprocess.run", return_value=SimpleNamespace(returncode=0, stdout="dcc-mcp-cli 0.20.45\n", stderr="")
    ) as process:
        assert launcher().main(["start", "--config", str(config)]) == 2
    assert process.call_count == 2
    assert not Path(data["work_dir"]).exists()
    assert "gateway_bin must be dcc-mcp-server" in capsys.readouterr().err


def test_missing_core_dependencies_are_rejected_before_any_lifecycle_command(tmp_path, capsys):
    config, data = config_file(tmp_path)
    empty = tmp_path / "empty-dependencies"
    empty.mkdir()
    data["site_packages"] = str(empty)
    config.write_text(json.dumps(data))
    with patch("subprocess.run") as process:
        assert launcher().main(["start", "--config", str(config), "--dry-run"]) == 2
    process.assert_not_called()
    assert not Path(data["work_dir"]).exists()
    assert "site_packages" in capsys.readouterr().err
