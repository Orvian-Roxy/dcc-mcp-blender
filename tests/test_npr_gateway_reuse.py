"""Regression: a shared MCP gateway must not be rebound or started per task."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.test_npr_runtime import config_file, launcher


def test_shared_gateway_is_reused(tmp_path):
    _, c = config_file(tmp_path)
    c["shared_gateway"] = True
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if command[-1] == "--version":
            name = "dcc-mcp-cli" if command[0] == c["cli_path"] else "dcc-mcp-server"
            return SimpleNamespace(returncode=0, stdout=name + " 0.20.45", stderr="")
        host = dict(
            instance_id="test",
            binary_path=c["binary_path"],
            binary_sha256=c["binary_sha256"],
            build=c["expected_build"],
            background=False,
            adapter="0.2.21",
            core="0.20.45",
            server="0.20.45",
        )
        Path(c["work_dir"], "host.json").write_text(json.dumps(host))
        return SimpleNamespace(returncode=0, stdout=json.dumps({"ready": True, "instance_id": "test"}), stderr="")

    with patch("socket.socket") as sock, patch("subprocess.run", side_effect=run):
        result = launcher().start(c)
    assert result["ready"]
    sock.assert_not_called()
    assert not any("ensure" in cmd for cmd in calls)
    assert sum("start-instance" in cmd for cmd in calls) == 1


def test_shared_registry_reaches_cli_environment(tmp_path):
    _, config = config_file(tmp_path)
    config["registry_dir"] = str(tmp_path / "shared-registry")
    assert launcher().environment(config)["DCC_MCP_REGISTRY_DIR"] == config["registry_dir"]


def test_closed_instance_status_accepts_null_instance(tmp_path):
    _, config = config_file(tmp_path)
    work = Path(config['work_dir'])
    work.mkdir()
    config['base_url'] = 'http://127.0.0.1:9765'
    (work / 'config.json').write_text(json.dumps(config))
    (work / 'host.json').write_text(json.dumps({'instance_id': 'closed-instance'}))
    module = launcher()
    with patch.object(module, 'cli_run', return_value={'ready': False, 'instance': None}):
        result = module.status(work)
    assert result['ready'] is False
    assert result['instance_id'] == 'closed-instance'
    assert result['observation']['instance'] is None
