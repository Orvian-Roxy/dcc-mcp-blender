from __future__ import annotations

import hashlib
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from dcc_mcp_blender.extension_packages import (
    ExtensionPackageError,
    install_extension_package,
    plan_extension_install,
)


def _extension_zip(path: Path) -> Path:
    manifest = """
schema_version = "1.0.0"
id = "sample_extension"
version = "1.2.3"
name = "Sample Extension"
tagline = "Example"
maintainer = "Example"
type = "add-on"
blender_version_min = "4.5.0"
license = ["SPDX:GPL-3.0-or-later"]
"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("sample/blender_manifest.toml", manifest)
        archive.writestr("sample/__init__.py", "def register(): pass\n")
    return path


def test_plan_extension_package(tmp_path: Path) -> None:
    archive = _extension_zip(tmp_path / "sample.zip")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    plan = plan_extension_install(
        {
            "package_id": "sample_extension",
            "archive_path": str(archive),
            "sha256": digest,
        }
    )
    assert plan["package_kind"] == "extension"
    assert plan["version"] == "1.2.3"
    assert plan["manifest_path"] == "sample/blender_manifest.toml"


def test_plan_legacy_addon(tmp_path: Path) -> None:
    archive = tmp_path / "legacy.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("legacy_addon/__init__.py", "bl_info = {'name': 'Legacy'}\n")
    plan = plan_extension_install({"archive_path": str(archive)})
    assert plan["package_kind"] == "legacy_addon"
    assert plan["package_id"] == "legacy_addon"


def test_legacy_plan_reads_literal_identity_and_compatibility_without_execution(tmp_path):
    addon = tmp_path / "legacy.py"
    addon.write_text("bl_info = {'name': 'Legacy', 'version': (2, 2, 4), 'blender': (4, 5, 0)}\nraise RuntimeError()\n")
    plan = plan_extension_install({"archive_path": str(addon)})
    assert plan["version"] == "2.2.4"
    assert plan["blender_version_min"] == "4.5.0"
    addon.write_text("bl_info = dict(name='computed')\n")
    with pytest.raises(ExtensionPackageError, match="literal"):
        plan_extension_install({"archive_path": str(addon)})


def test_rejects_unsafe_archive_path(tmp_path: Path) -> None:
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("../escape/__init__.py", "")
    with pytest.raises(ExtensionPackageError, match="Unsafe archive path"):
        plan_extension_install({"archive_path": str(archive)})


def test_install_requires_pinned_hash_before_touching_blender(tmp_path):
    archive = _extension_zip(tmp_path / "sample.zip")
    with patch.dict(sys.modules, {"bpy": SimpleNamespace()}):
        with pytest.raises(ExtensionPackageError, match="sha256"):
            install_extension_package({"archive_path": str(archive), "package_id": "sample_extension"})


@pytest.mark.parametrize("source", ["missing.zip", "directory.zip", "unsupported.tar"])
def test_plan_rejects_invalid_local_source(tmp_path, source):
    path = tmp_path / source
    if source == "directory.zip":
        path.mkdir()
    elif source == "unsupported.tar":
        path.write_bytes(b"not a Blender package")
    with pytest.raises(ExtensionPackageError):
        plan_extension_install({"archive_path": str(path)})


def _pinned(path, package_id):
    return {
        "archive_path": str(path),
        "package_id": package_id,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def test_installs_extension_in_explicit_repo_and_enables_current_process_only(tmp_path):
    archive = _extension_zip(tmp_path / "sample.zip")
    install_operator = MagicMock(return_value={"FINISHED"})
    module = "bl_ext.isolated.sample_extension"
    state = {"installed": False, "enabled": False}

    def install(*args, **kwargs):
        state["installed"] = True
        return {"FINISHED"}

    def enable(name, default_set, persistent, handle_error):
        assert name == module
        assert default_set is persistent is False
        state["enabled"] = True
        return SimpleNamespace(__name__=name)

    install_operator.side_effect = install
    repo = SimpleNamespace(enabled=True, source="USER", module="isolated", directory=str(tmp_path / "extensions"))
    bpy = SimpleNamespace(
        app=SimpleNamespace(version=(5, 2, 0)),
        context=SimpleNamespace(preferences=SimpleNamespace(extensions=SimpleNamespace(repos=[repo]))),
        ops=SimpleNamespace(extensions=SimpleNamespace(package_install_files=install_operator)),
    )
    addon_utils = SimpleNamespace(
        modules=lambda refresh=False: [SimpleNamespace(__name__=module)] if state["installed"] else [],
        check=lambda name: (False, state["enabled"]),
        enable=enable,
    )
    with patch.dict(sys.modules, {"bpy": bpy, "addon_utils": addon_utils}):
        with pytest.raises(ExtensionPackageError, match="explicit.*repository"):
            install_extension_package(_pinned(archive, "sample_extension"))
        wrong_hash = {**_pinned(archive, "sample_extension"), "sha256": "0" * 64}
        with pytest.raises(ExtensionPackageError, match="checksum mismatch"):
            install_extension_package(wrong_hash, repository="isolated")
        bpy.app.version = (4, 0, 0)
        with pytest.raises(ExtensionPackageError, match="requires Blender"):
            install_extension_package(_pinned(archive, "sample_extension"), repository="isolated")
        bpy.app.version = (5, 2, 0)
        install_operator.assert_not_called()
        result = install_extension_package(_pinned(archive, "sample_extension"), repository="isolated", enable=True)
    assert result["addon_modules"] == [module]
    assert result["addon_states"][0]["enabled_default"] is False
    assert result["addon_states"][0]["enabled_runtime"] is True
    install_operator.assert_called_once_with(
        "EXEC_DEFAULT",
        filepath=str(archive.resolve()),
        repo="isolated",
        enable_on_install=False,
        overwrite=False,
    )


@pytest.mark.parametrize("identity", ["legacy_addon", "MBTs-NG", "Another-Legacy-Addon"])
def test_installs_legacy_in_explicit_current_profile_without_enabling(tmp_path, identity):
    archive = tmp_path / "legacy.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr(identity + "/__init__.py", "bl_info = {'name': 'Legacy', 'blender': (4, 5, 0)}\n")
    install_operator = MagicMock(return_value={"FINISHED"})
    target = tmp_path / "profile" / "addons"
    modules = []

    def install(*args, **kwargs):
        modules.append(SimpleNamespace(__name__=identity))
        return {"FINISHED"}

    install_operator.side_effect = install
    bpy = SimpleNamespace(
        app=SimpleNamespace(version=(5, 2, 0)),
        utils=SimpleNamespace(user_resource=lambda *a, **kw: str(target)),
        ops=SimpleNamespace(preferences=SimpleNamespace(addon_install=install_operator)),
    )
    addon_utils = SimpleNamespace(modules=lambda refresh=False: modules, check=lambda name: (False, False))
    with patch.dict(sys.modules, {"bpy": bpy, "addon_utils": addon_utils}):
        with pytest.raises(ExtensionPackageError, match="target_directory"):
            install_extension_package(_pinned(archive, identity))
        result = install_extension_package(_pinned(archive, identity), target_directory=str(target))
        with pytest.raises(ExtensionPackageError, match="already"):
            install_extension_package(_pinned(archive, identity), target_directory=str(target))
    assert result["addon_modules"] == [identity]
    assert result["target_directory"] == str(target)
    install_operator.assert_called_once_with("EXEC_DEFAULT", filepath=str(archive.resolve()), overwrite=False)


def test_rejects_control_addon_before_install(tmp_path):
    addon = tmp_path / "dcc_mcp_blender.py"
    addon.write_text("bl_info = {'name': 'Control'}\n")
    with patch.dict(sys.modules, {"bpy": SimpleNamespace()}):
        with pytest.raises(ValueError, match="control"):
            install_extension_package(_pinned(addon, "dcc_mcp_blender"))


def test_cancelled_native_install_is_not_success(tmp_path):
    addon = tmp_path / "cancelled_addon.py"
    addon.write_text("bl_info = {'name': 'Cancelled'}\n")
    target = tmp_path / "profile" / "addons"
    bpy = SimpleNamespace(
        app=SimpleNamespace(version=(5, 2, 0)),
        utils=SimpleNamespace(user_resource=lambda *args, **kwargs: str(target)),
        ops=SimpleNamespace(preferences=SimpleNamespace(addon_install=lambda *args, **kwargs: {"CANCELLED"})),
    )
    addon_utils = SimpleNamespace(modules=lambda refresh=False: [], check=lambda name: (False, False))
    with patch.dict(sys.modules, {"bpy": bpy, "addon_utils": addon_utils}):
        with pytest.raises(ExtensionPackageError, match="did not finish"):
            install_extension_package(_pinned(addon, "cancelled_addon"), target_directory=str(target))
