import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch


def load():
    tools = Path(__file__).parents[1] / "tools"
    sys.path.insert(0, str(tools))
    try:
        spec = importlib.util.spec_from_file_location("npr_mcp", tools / "npr_mcp.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(tools))


def test_start_only_delegates_to_existing_launcher(tmp_path):
    module = load()
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"binary_path": "/selected/Blender", "project_root": "/old"}))
    service = module.InstanceLauncher(config)
    with patch.object(module.npr_runtime, "start", return_value={"ready": True}) as start:
        assert service.start_instance("test-01", str(tmp_path)) == {"ready": True}
    passed = start.call_args.args[0]
    assert passed["binary_path"] == "/selected/Blender"
    assert passed["project_root"] == str(tmp_path)
    assert passed["shared_gateway"] is True
    assert passed["gateway_port"] == 9765
    assert passed["work_dir"] == str(tmp_path / "instances/test-01")


def test_status_never_launches_and_name_stays_within_instances(tmp_path):
    import pytest

    module = load()
    service = module.InstanceLauncher(tmp_path / "config.json")
    with patch.object(module.npr_runtime, "status", return_value={"ready": False}) as status, patch.object(
        module.npr_runtime, "start"
    ) as start:
        assert service.instance_status("test-01") == {"ready": False}
        status.assert_called_once_with(tmp_path / "instances/test-01")
        with pytest.raises(ValueError):
            service.instance_status("../escape")
        start.assert_not_called()
