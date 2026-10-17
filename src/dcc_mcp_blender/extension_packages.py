"""Inspection and installation helpers for Blender add-on packages."""

from __future__ import annotations

import ast
import hashlib
import os
import re
import stat
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Mapping, Optional, Tuple

MAX_ARCHIVE_FILES = int(os.environ.get("DCC_MCP_BLENDER_MAX_ARCHIVE_FILES", "10000"))
MAX_UNCOMPRESSED_BYTES = int(os.environ.get("DCC_MCP_BLENDER_MAX_UNCOMPRESSED_BYTES", str(2 * 1024**3)))
PACKAGE_KINDS = {"auto", "extension", "legacy_addon"}
MANIFEST_FIELDS = {
    "id",
    "version",
    "type",
    "blender_version_min",
    "blender_version_max",
}
MANIFEST_VALUE = re.compile(r"^(id|version|type|blender_version_min|blender_version_max)\s*=\s*(['\"])(.*?)\2")


class ExtensionPackageError(ValueError):
    """Raised when a Blender package is invalid or cannot be installed safely."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_name(raw_name: str) -> PurePosixPath:
    name = PurePosixPath(raw_name.replace("\\", "/"))
    if name.is_absolute() or not name.parts or ".." in name.parts:
        raise ExtensionPackageError(f"Unsafe archive path: {raw_name}")
    if ":" in name.parts[0]:
        raise ExtensionPackageError(f"Unsafe archive path: {raw_name}")
    return name


def _archive_members(archive: zipfile.ZipFile) -> List[Tuple[zipfile.ZipInfo, PurePosixPath]]:
    members = []
    total_size = 0
    for info in archive.infolist():
        if info.is_dir():
            continue
        mode = info.external_attr >> 16
        if stat.S_IFMT(mode) == stat.S_IFLNK:
            raise ExtensionPackageError(f"Archive symlinks are not supported: {info.filename}")
        members.append((info, _safe_name(info.filename)))
        total_size += info.file_size
    if not members:
        raise ExtensionPackageError("Archive contains no files")
    if len(members) > MAX_ARCHIVE_FILES:
        raise ExtensionPackageError(f"Archive contains {len(members)} files; limit is {MAX_ARCHIVE_FILES}")
    if total_size > MAX_UNCOMPRESSED_BYTES:
        raise ExtensionPackageError(f"Archive expands to {total_size} bytes; limit is {MAX_UNCOMPRESSED_BYTES}")
    return members


def _parse_manifest(content: str) -> Dict[str, str]:
    manifest = {}
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if line.startswith("["):
            break
        match = MANIFEST_VALUE.match(line)
        if match and match.group(1) in MANIFEST_FIELDS:
            manifest[match.group(1)] = match.group(3)
    missing = {"id", "version", "type", "blender_version_min"} - set(manifest)
    if missing:
        raise ExtensionPackageError("Blender extension manifest is missing: " + ", ".join(sorted(missing)))
    if manifest["type"] != "add-on":
        raise ExtensionPackageError(f"Only Blender add-on extensions are supported, got {manifest['type']!r}")
    return manifest


def _legacy_metadata(source: bytes) -> Dict[str, Any]:
    if len(source) > 2 * 1024**2:
        raise ExtensionPackageError("Legacy metadata entrypoint exceeds 2 MiB")
    for node in ast.parse(source.decode("utf-8-sig")).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "bl_info" for t in node.targets):
            try:
                info = ast.literal_eval(node.value)
            except (ValueError, TypeError) as exc:
                raise ExtensionPackageError("Legacy bl_info must be a literal dictionary") from exc
            if not isinstance(info, dict):
                raise ExtensionPackageError("Legacy bl_info must be a literal dictionary")

            def version(value):
                return ".".join(str(part) for part in value) if isinstance(value, (tuple, list)) else value

            return {
                "name": info.get("name"),
                "author": info.get("author"),
                "version": version(info.get("version")),
                "blender_version_min": version(info.get("blender")),
            }
    raise ExtensionPackageError("Legacy add-on requires a literal bl_info dictionary")


def _inspect_zip(path: Path) -> Dict[str, Any]:
    with zipfile.ZipFile(path) as archive:
        members = _archive_members(archive)
        manifest_members = [(info, name) for info, name in members if name.name == "blender_manifest.toml"]
        if len(manifest_members) > 1:
            raise ExtensionPackageError("Archive contains multiple Blender extension manifests")
        if manifest_members:
            manifest_info, manifest_name = manifest_members[0]
            manifest = _parse_manifest(archive.read(manifest_info).decode("utf-8-sig"))
            return {
                "package_kind": "extension",
                "package_id": manifest["id"],
                "version": manifest["version"],
                "blender_version_min": manifest["blender_version_min"],
                "blender_version_max": manifest.get("blender_version_max"),
                "manifest_path": manifest_name.as_posix(),
                "file_count": len(members),
                "total_uncompressed_bytes": sum(info.file_size for info, _ in members),
            }

        addon_roots = sorted(
            {name.parts[0] for _, name in members if len(name.parts) == 2 and name.name == "__init__.py"}
        )
        if len(addon_roots) != 1:
            raise ExtensionPackageError("Legacy add-on ZIP must contain exactly one top-level package with __init__.py")
        if any(name.parts[0] != addon_roots[0] for _, name in members):
            raise ExtensionPackageError("Legacy ZIP must contain only its declared top-level package")
        entrypoint = next(info for info, name in members if name.as_posix() == addon_roots[0] + "/__init__.py")
        if entrypoint.file_size > 2 * 1024**2:
            raise ExtensionPackageError("Legacy metadata entrypoint exceeds 2 MiB")
        return {
            "package_kind": "legacy_addon",
            "package_id": addon_roots[0],
            **_legacy_metadata(archive.read(entrypoint)),
            "blender_version_max": None,
            "manifest_path": None,
            "file_count": len(members),
            "total_uncompressed_bytes": sum(info.file_size for info, _ in members),
        }


def _package_value(package: Mapping[str, Any], key: str, default: Any = None) -> Any:
    value = package.get(key, default)
    return value if value not in (None, "") else default


def plan_extension_install(package: Mapping[str, Any]) -> Dict[str, Any]:
    """Inspect a downloaded Blender add-on package without modifying Blender."""
    path = Path(str(_package_value(package, "archive_path", ""))).expanduser().resolve()
    supplied_id = str(_package_value(package, "package_id", "")).strip()
    if not path.is_file():
        raise ExtensionPackageError(f"Package file not found: {path}")
    if path.suffix.lower() not in {".zip", ".py"}:
        raise ExtensionPackageError("Blender add-on package must be a .zip or .py file")

    actual_sha256 = _sha256(path)
    expected_sha256 = str(_package_value(package, "sha256", ""))
    if expected_sha256.startswith("sha256:"):
        expected_sha256 = expected_sha256[len("sha256:") :]
    if expected_sha256 and expected_sha256.lower() != actual_sha256.lower():
        raise ExtensionPackageError(f"Package checksum mismatch: expected {expected_sha256}, got {actual_sha256}")

    if path.suffix.lower() == ".zip":
        if not zipfile.is_zipfile(path):
            raise ExtensionPackageError(f"Package is not a ZIP archive: {path}")
        inspected = _inspect_zip(path)
    else:
        inspected = {
            "package_kind": "legacy_addon",
            "package_id": path.stem,
            **_legacy_metadata(path.read_bytes()),
            "blender_version_max": None,
            "manifest_path": None,
            "file_count": 1,
            "total_uncompressed_bytes": path.stat().st_size,
        }

    requested_kind = str(_package_value(package, "package_kind", "auto"))
    if requested_kind not in PACKAGE_KINDS:
        raise ExtensionPackageError(f"Unsupported package_kind: {requested_kind}")
    if requested_kind != "auto" and inspected["package_kind"] != requested_kind:
        raise ExtensionPackageError(
            f"Package kind mismatch: expected {requested_kind}, detected {inspected['package_kind']}"
        )
    if supplied_id and supplied_id != inspected["package_id"]:
        raise ExtensionPackageError(f"Package ID mismatch: expected {supplied_id}, detected {inspected['package_id']}")
    return {
        **inspected,
        "archive_path": str(path),
        "sha256": actual_sha256,
        "requested_kind": requested_kind,
    }


def _version_tuple(value: str) -> Tuple[int, ...]:
    return tuple(int(number) for number in re.findall(r"\d+", value or ""))


def _user_repositories(bpy: Any) -> List[Dict[str, str]]:
    preferences = getattr(getattr(bpy, "context", None), "preferences", None)
    extensions = getattr(preferences, "extensions", None)
    repos = getattr(extensions, "repos", [])
    result = []
    for repo in repos:
        if not bool(getattr(repo, "enabled", False)):
            continue
        if str(getattr(repo, "source", "")) == "SYSTEM":
            continue
        module = str(getattr(repo, "module", ""))
        directory = str(getattr(repo, "directory", ""))
        if module and directory:
            result.append(
                {
                    "module": module,
                    "name": str(getattr(repo, "name", module)),
                    "directory": directory,
                }
            )
    return result


def _select_repository(bpy: Any, requested: Optional[str]) -> Dict[str, str]:
    if not requested:
        raise ExtensionPackageError("Installation requires an explicit repository module")
    selected = next((repo for repo in _user_repositories(bpy) if repo["module"] == requested), None)
    if selected is None:
        raise ExtensionPackageError(f"Enabled Blender user repository not found: {requested}")
    return selected


def _addon_module_names(refresh: bool) -> List[str]:
    import addon_utils

    return sorted(
        str(module.__name__) for module in addon_utils.modules(refresh=refresh) if getattr(module, "__name__", None)
    )


def _addon_states(module_names: List[str]) -> List[Dict[str, Any]]:
    from dcc_mcp_blender._dev_ops import _addon_status_data

    return [_addon_status_data(name) for name in module_names]


def _check_blender_compatibility(plan: Mapping[str, Any], version: Tuple[int, ...]) -> None:
    minimum = plan.get("blender_version_min")
    maximum = plan.get("blender_version_max")
    if minimum and version < _version_tuple(str(minimum)):
        raise ExtensionPackageError(f"Package requires Blender {minimum} or newer")
    if maximum and version >= _version_tuple(str(maximum)):
        raise ExtensionPackageError(f"Package does not support Blender {maximum} or newer")


def install_extension_package(
    package: Mapping[str, Any],
    repository: Optional[str] = None,
    enable: bool = False,
    overwrite: bool = False,
    persist: bool = False,
    target_directory: Optional[str] = None,
) -> Dict[str, Any]:
    """Install a planned package with Blender's supported preference operators."""
    import bpy

    if not re.fullmatch(r"(?:sha256:)?[A-Fa-f0-9]{64}", str(package.get("sha256", ""))):
        raise ExtensionPackageError("Installation requires an explicit sha256 digest")
    if not package.get("package_id"):
        raise ExtensionPackageError("Installation requires an explicit package_id")
    from dcc_mcp_blender._dev_ops import _validate_addon_module, enable_addon

    plan = plan_extension_install(package)
    _validate_addon_module(str(plan["package_id"]), mutate=True)
    blender_version = tuple(bpy.app.version[:3])
    _check_blender_compatibility(plan, blender_version)
    before = set(_addon_module_names(refresh=True))

    selected_repo = None
    if plan["package_kind"] == "extension":
        selected_repo = _select_repository(bpy, repository)
        module_name = f"bl_ext.{selected_repo['module']}.{plan['package_id']}"
        installed_path = Path(selected_repo["directory"]) / str(plan["package_id"])
    else:
        actual_directory = Path(bpy.utils.user_resource("SCRIPTS", path="addons")).resolve()
        if not target_directory or Path(target_directory).expanduser().resolve() != actual_directory:
            raise ExtensionPackageError(f"Explicit target_directory must match the current profile: {actual_directory}")
        target_directory = str(actual_directory)
        module_name = str(plan["package_id"])
        installed_path = actual_directory / module_name
    _validate_addon_module(module_name, mutate=True)
    from dcc_mcp_blender._dev_ops import _addon_status_data

    if not overwrite and (
        module_name in before
        or installed_path.exists()
        or installed_path.with_suffix(".py").exists()
        or _addon_status_data(module_name)["discoverable"]
    ):
        raise ExtensionPackageError(f"Add-on already exists; use enable_addon for an installed package: {module_name}")
    if plan["package_kind"] == "extension":
        result = bpy.ops.extensions.package_install_files(
            "EXEC_DEFAULT",
            filepath=plan["archive_path"],
            repo=selected_repo["module"],
            enable_on_install=False,
            overwrite=overwrite,
        )
    else:
        result = bpy.ops.preferences.addon_install("EXEC_DEFAULT", filepath=plan["archive_path"], overwrite=overwrite)

    if "FINISHED" not in set(result):
        raise ExtensionPackageError(f"Blender package install did not finish: {sorted(result)}")

    after = set(_addon_module_names(refresh=True))
    if module_name not in after and not _addon_status_data(module_name)["discoverable"]:
        raise ExtensionPackageError(f"Blender reported success but add-on module was not found: {module_name}")
    modules = [module_name]
    enabled_result = enable_addon(module_name, persist=persist) if enable else None
    states = _addon_states(modules)
    if enabled_result and not enabled_result["success"]:
        # Carry host readback through the skill wrapper, including persistence failures.
        error = ExtensionPackageError(f"Installed, but add-on activation failed: {enabled_result['message']}")
        error.context = dict(installed=True, addon_modules=modules, addon_states=states, activation=enabled_result)
        raise error
    return {
        **plan,
        "installed": True,
        "enabled_requested": enable,
        "overwrite": overwrite,
        "repository": selected_repo,
        "target_directory": target_directory or (selected_repo["directory"] if selected_repo else None),
        "persistence_requested": persist,
        "preferences_saved": bool(enabled_result and enabled_result["context"]["preferences_saved"]),
        "addon_modules": modules,
        "addon_states": states,
        "new_addon_modules": sorted(after - before),
        "blender_version": ".".join(str(part) for part in blender_version),
    }
