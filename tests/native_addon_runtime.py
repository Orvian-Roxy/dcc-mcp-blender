"""Run #12 inside a coordinator-owned private NPR; never launch or clean up.

run() adds one local repository only to the current preferences in memory,
installs the supplied pinned package there, and retains all installed artifacts.
It leaves the Extension enabled for subsequent coordinator-owned checks.
"""

from __future__ import annotations

import hashlib
import importlib
import os
import sys
import threading
import zipfile
from pathlib import Path
from uuid import uuid4

from dcc_mcp_blender._dev_ops import disable_addon, enable_addon, get_addon_status
from dcc_mcp_blender.extension_packages import install_extension_package, plan_extension_install


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def run(archive_path: str, sha256: str, repo_directory: str) -> dict:
    """Exercise actual registration, native installation, wheel resolution and readback."""
    import bpy

    assert threading.current_thread() is threading.main_thread()
    profile = {}
    for kind, variable in (("CONFIG", "BLENDER_USER_CONFIG"), ("SCRIPTS", "BLENDER_USER_SCRIPTS")):
        configured = os.environ.get(variable)
        assert configured and Path(configured).is_absolute(), f"Explicit private {variable} required"
        actual = Path(bpy.utils.user_resource(kind)).resolve()
        assert actual == Path(configured).resolve(), f"Profile mismatch: {variable}"
        profile[kind] = str(actual)
    extension_root = Path(os.environ["BLENDER_USER_EXTENSIONS"]).resolve()
    directory = Path(repo_directory).expanduser().resolve()
    assert extension_root in directory.parents, "Repository must be inside the private extension root"
    assert not (directory / "mmd_tools").exists(), "Use a fresh directory; retain previous artifacts"
    archive = Path(archive_path).resolve(strict=True)
    assert _digest(archive) == sha256
    package = {"archive_path": str(archive), "package_id": "mmd_tools", "sha256": sha256}
    plan = plan_extension_install(package)
    assert plan["package_kind"] == "extension" and plan["version"] == "4.5.14", plan
    preferences_file = Path(profile["CONFIG"]) / "userpref.blend"
    preferences_before = _digest(preferences_file)
    defaults_before = sorted(bpy.context.preferences.addons.keys())
    records = []
    cases = {}

    def invoke(name, function, *args, **kwargs):
        result = function(*args, **kwargs)
        records.append({"operation": name, "result": result})
        assert result["success"], result
        return result["context"]

    try:
        before = invoke("wiggle.inspect.before", get_addon_status, "wiggle_2")
        assert before["catalog_member"] or before["discoverable"], before
        assert list(before["version"] or []) == [2, 2, 4], before
        if before["enabled_runtime"]:
            invoke("wiggle.disable.baseline", disable_addon, "wiggle_2")
        baseline = invoke("wiggle.inspect.disabled", get_addon_status, "wiggle_2")
        assert baseline["enabled_runtime"] is False
        enabled = invoke("wiggle.enable", enable_addon, "wiggle_2")
        assert enabled["after"]["enabled_runtime"] is True
        assert enabled["after"]["enabled_default"] == before["enabled_default"]
        assert enabled["preferences_saved"] is False
        disabled = invoke("wiggle.disable", disable_addon, "wiggle_2")
        assert disabled["after"]["enabled_runtime"] is False
        assert disabled["after"]["enabled_default"] == before["enabled_default"]
        assert disabled["preferences_saved"] is False
        cases["wiggle"] = {"state": "passed", "before": before, "baseline": baseline, "after": disabled["after"]}
    except Exception as exc:
        cases["wiggle"] = {"state": "failed", "error": f"{type(exc).__name__}: {exc}"}

    repository = None
    try:
        legacy = invoke("mmd.legacy.inspect", get_addon_status, "mmd_tools")
        # Avoid simultaneous registration of legacy and Extension classes in this private host.
        if legacy["enabled_runtime"]:
            invoke("mmd.legacy.disable", disable_addon, "mmd_tools")
        directory.mkdir(parents=True, exist_ok=True)
        module = "dcc_acceptance_" + uuid4().hex[:8]
        # Blender 5.2 bl_pkg/bl_extension_cli.py subcmd_repo.add uses this public RNA API.
        repo = bpy.context.preferences.extensions.repos.new(
            name="DCC acceptance " + module,
            module=module,
            custom_directory=str(directory),
            remote_url="",
            source="USER",
        )
        repository = {"module": repo.module, "directory": repo.directory, "enabled": repo.enabled}
        assert repo.enabled and Path(repo.directory).resolve() == directory, repository
        result = install_extension_package(package, repository=repo.module, enable=True)
        records.append({"operation": "mmd.install_extension", "result": result})
        identity = "bl_ext." + repo.module + ".mmd_tools"
        assert result["addon_modules"] == [identity], result
        status = invoke("mmd.extension.inspect", get_addon_status, identity, operators=["mmd_tools.import_model"])
        assert status["enabled_runtime"] is True and status["enabled_default"] is False, status
        assert status["operators"] == {"mmd_tools.import_model": True}, status
        installed = Path(sys.modules[identity].__file__).resolve()
        assert directory / "mmd_tools" in installed.parents, str(installed)
        bundled = []
        with zipfile.ZipFile(archive) as package_zip:
            for name in package_zip.namelist():
                if name.endswith(".whl"):
                    wheel = directory / "mmd_tools" / name
                    digest = hashlib.sha256(package_zip.read(name)).hexdigest()
                    assert _digest(wheel) == digest, str(wheel)
                    bundled.append({"path": str(wheel), "sha256": digest})
        assert bundled, "Official package did not preserve its bundled wheel"
        opencc = importlib.import_module("opencc")
        wheel_import = Path(opencc.__file__).resolve()
        assert extension_root in wheel_import.parents, str(wheel_import)
        assert opencc.OpenCC("s2t").convert("简体") == "簡體"
        cases["mmd_extension"] = {
            "state": "passed",
            "status": status,
            "legacy_before": legacy,
            "install": result,
            "bundled_wheels": bundled,
            "wheel_import": str(wheel_import),
        }
    except Exception as exc:
        cases["mmd_extension"] = {"state": "failed", "error": f"{type(exc).__name__}: {exc}"}

    preferences_after = _digest(preferences_file)
    defaults_after = sorted(bpy.context.preferences.addons.keys())
    cases["preferences"] = {
        "state": "passed"
        if preferences_after == preferences_before and defaults_after == defaults_before
        else "failed",
        "path": str(preferences_file),
        "before_sha256": preferences_before,
        "after_sha256": preferences_after,
        "defaults_before": defaults_before,
        "defaults_after": defaults_after,
    }
    return {
        "success": all(case["state"] == "passed" for case in cases.values()),
        "profile": profile,
        "repository": repository,
        "package": plan,
        "blender_version": bpy.app.version_string,
        "blender_build": bpy.app.build_hash.decode(),
        "cases": cases,
        "records": records,
    }
