"""Public skill boundary for the optional existing native MMD provider."""

import sys
from concurrent.futures import ThreadPoolExecutor
from types import ModuleType

import pytest
from conftest import load_skill_script


def test_discovery_is_available_without_importing_provider(monkeypatch):
    monkeypatch.setitem(sys.modules, "runtime_native.mmd_dispatch", None)
    result = load_skill_script("blender-mmd-runtime", "list_methods").main()
    methods = {entry["method"]: entry for entry in result["context"]["methods"]}
    assert len(methods) == 16
    assert methods["mmd.toon.apply"]["phase"] == "modify"
    assert methods["mmd.wiggle.restore"]["phase"] == "restore"
    assert not any("worker" in method for method in methods)


def test_call_preserves_provider_conflict_and_request(monkeypatch):
    provider = ModuleType("runtime_native.mmd_dispatch")
    expected = {"status": "error", "code": "STATE_DRIFT", "data": {"changed": ["Bone"]}}
    received = []
    provider.execute = lambda method, parameters: received.append((method, parameters)) or expected
    monkeypatch.setitem(sys.modules, "runtime_native.mmd_dispatch", provider)
    request = {"request": {"bone_names": ["Bone"], "transaction_id": "receipt"}}
    result = load_skill_script("blender-mmd-runtime", "call").main(method="mmd.wiggle.restore", parameters=request)
    assert result == expected
    assert received == [("mmd.wiggle.restore", request)]


@pytest.mark.parametrize(
    "method,parameters",
    [
        ("mmd.worker.execute", {}),
        ("mmd.toon.apply", []),
        ("mmd.toon.apply", {"request": "not an object"}),
        ("mmd.toon.apply", {"request": {}, "unexpected": True}),
        ("mmd.toon.capabilities", {"request": {}}),
        ("mmd.inspect", {"request": {}}),
        ("mmd.toon.apply", {"request": {"value": float("nan")}}),
    ],
)
def test_rejects_invalid_call_before_provider_import(method, parameters):
    result = load_skill_script("blender-mmd-runtime", "call").main(method=method, parameters=parameters)
    assert result["success"] is False
    assert "attach_project" not in str(result)


def test_missing_provider_gives_existing_attach_project_remedy(monkeypatch):
    monkeypatch.setitem(sys.modules, "runtime_native.mmd_dispatch", None)
    result = load_skill_script("blender-mmd-runtime", "call").main(method="mmd.toon.capabilities")
    assert result["success"] is False
    assert "attach_project" in str(result)


def test_call_refuses_background_thread():
    call = load_skill_script("blender-mmd-runtime", "call").main
    with ThreadPoolExecutor(max_workers=1) as executor:
        result = executor.submit(call, method="mmd.toon.capabilities").result()
    assert result["success"] is False
    assert "main thread" in str(result)


def test_skill_manifest_matches_discovered_public_methods():
    from pathlib import Path

    import yaml
    from dcc_mcp_core import validate_skill

    skill = Path(__file__).resolve().parents[1] / "src/dcc_mcp_blender/skills/blender-mmd-runtime"
    report = validate_skill(str(skill))
    assert not report.has_errors, report.issues
    tools = {entry["name"]: entry for entry in yaml.safe_load((skill / "tools.yaml").read_text())["tools"]}
    discovered = load_skill_script("blender-mmd-runtime", "list_methods").main()["context"]["methods"]
    assert set(tools["call"]["input_schema"]["properties"]["method"]["enum"]) == {
        entry["method"] for entry in discovered
    }
    assert tools["call"]["affinity"] == "main"
    assert tools["call"]["read_only"] is False
