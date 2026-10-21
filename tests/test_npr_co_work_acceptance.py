"""CLI boundary of the isolated NPR acceptance runner."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


def test_cli_is_pinned_and_receipt_keeps_one_result(tmp_path):
    path = Path(__file__).parents[1] / "tools" / "npr_co_work_acceptance.py"
    spec = importlib.util.spec_from_file_location("npr_co_work_acceptance", path)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    receipt = tmp_path / "calls.json"
    cli = runner.Cli(
        {"cli_path": "/test/dcc-mcp-cli", "base_url": "http://127.0.0.1:9789", "instance_id": "test-instance"},
        receipt,
    )
    actual = {"success": True, "context": {"save_status": "saved"}}
    duplicate = {"output": {"structuredContent": actual, "content": [{"type": "text", "text": json.dumps(actual)}]}}
    with patch.object(
        runner.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout=json.dumps(duplicate), stderr="")
    ) as invoke:
        assert cli.call("blender_scene__save_scene", {"filepath": str(tmp_path / "scene.blend")}, "mcp") == actual
    command = invoke.call_args.args[0]
    assert command[0] == "/test/dcc-mcp-cli"
    assert command[command.index("--transport") + 1] == "mcp"
    assert command[command.index("call") + 1] == "call"
    assert json.loads(command[command.index("--json") + 1])["tool_slug"] == "blender.testinst.blender_scene__save_scene"
    assert "--instance-id" not in command
    assert "--dcc-type" not in command
    assert "--no-auto-gateway" in command
    record = json.loads(receipt.read_text())[0]
    assert record["result"] == actual
    assert "content" not in record["result"]
    assert record["cli_wall_seconds"] >= 0


def test_gateway_workflow_text_only_result_is_parsed_once():
    path = Path(__file__).parents[1] / "tools" / "npr_co_work_acceptance.py"
    spec = importlib.util.spec_from_file_location("npr_co_work_acceptance", path)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    result = {"success": True, "context": {"save_status": "saved"}}
    response = {"output": {"content": [{"type": "text", "text": json.dumps({"output": result})}], "isError": False}}
    assert runner.canonical(response) == result
