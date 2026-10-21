"""Development and diagnostics helpers for Blender MCP workflows."""

from __future__ import annotations

import importlib
import importlib.machinery
import importlib.metadata
import io
import platform
import re
import runpy
import shutil
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import ModuleType
from typing import Any, Dict, Iterable, List, Optional, Tuple

from dcc_mcp_core.skill import skill_error, skill_exception, skill_success

_DEBUG_SERVER: Dict[str, Any] = {}


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    return repr(value)


def _module_version(package_name: str) -> Optional[str]:
    try:
        return importlib.metadata.version(package_name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _iterable(value: Any) -> Iterable[Any]:
    if value is None:
        return []
    try:
        return list(value)
    except TypeError:
        return []


def _package_versions() -> Dict[str, Optional[str]]:
    return {
        "dcc-mcp-blender": _module_version("dcc-mcp-blender"),
        "dcc-mcp-core": _module_version("dcc-mcp-core"),
        "debugpy": _module_version("debugpy"),
        "pytest": _module_version("pytest"),
    }


def attach_project(project_root: str, extra_paths: Optional[List[str]] = None) -> dict:
    """Attach a project checkout to ``sys.path`` for live add-on debugging."""
    try:
        root_path = Path(project_root).expanduser().resolve()
        paths = [root_path]
        if extra_paths:
            for extra_path in extra_paths:
                candidate = Path(extra_path).expanduser()
                paths.append(candidate if candidate.is_absolute() else root_path / candidate)

        added = []
        existing = []
        missing = []
        for raw_path in paths:
            resolved = str(raw_path.resolve())
            if not Path(resolved).exists():
                missing.append(resolved)
                continue
            if resolved in sys.path:
                existing.append(resolved)
                continue
            sys.path.insert(0, resolved)
            added.append(resolved)

        return skill_success(
            "Attached project paths",
            project_root=str(root_path),
            added=added,
            existing=existing,
            missing=missing,
            sys_path_head=sys.path[:10],
        )
    except Exception as exc:
        return skill_exception(exc, message="Failed to attach project")


def reload_modules(
    module_names: Optional[List[str]] = None,
    package_prefix: Optional[str] = None,
) -> dict:
    """Reload explicit modules or loaded modules matching a package prefix."""
    try:
        targets: List[str] = []
        if module_names:
            targets.extend(module_names)
        if package_prefix:
            prefix = package_prefix.rstrip(".")
            targets.extend(name for name in sys.modules if name == prefix or name.startswith(f"{prefix}."))
        targets = sorted(set(targets), key=lambda name: name.count("."), reverse=True)
        if not targets:
            return skill_error(
                "No modules selected",
                "Pass module_names or package_prefix to reload development modules.",
            )

        reloaded = []
        imported = []
        errors = []
        for name in targets:
            try:
                module = sys.modules.get(name)
                if module is None:
                    importlib.import_module(name)
                    imported.append(name)
                else:
                    importlib.reload(module)
                    reloaded.append(name)
            except Exception as exc:
                errors.append({"module": name, "error": repr(exc), "traceback": traceback.format_exc()})

        if errors:
            return skill_error(
                "Some modules failed to reload",
                "One or more modules raised during import or reload.",
                reloaded=reloaded,
                imported=imported,
                errors=errors,
            )
        return skill_success(
            "Modules reloaded",
            reloaded=reloaded,
            imported=imported,
            count=len(reloaded) + len(imported),
        )
    except Exception as exc:
        return skill_exception(exc, message="Failed to reload modules")


def _check_import_module(args: Dict[str, Any]) -> dict:
    module_name = args.get("module") or args.get("module_name")
    if not module_name:
        return skill_error("Missing module", "Pass args.module for import_module.")
    module = importlib.import_module(str(module_name))
    return skill_success(
        f"Imported {module_name}",
        module=str(module_name),
        file=getattr(module, "__file__", None),
        package=getattr(module, "__package__", None),
    )


def _check_python_environment(args: Dict[str, Any]) -> dict:
    return get_python_environment(include_sys_path=bool(args.get("include_sys_path", False)))


def _check_addon_status(args: Dict[str, Any]) -> dict:
    addon_module = args.get("addon_module") or args.get("module")
    if not addon_module:
        return skill_error("Missing add-on module", "Pass args.addon_module for addon_status.")
    return get_addon_status(str(addon_module))


def _check_sleep(args: Dict[str, Any]) -> dict:
    seconds = float(args.get("seconds", 0))
    time.sleep(seconds)
    return skill_success("Sleep check completed", seconds=seconds)


_CHECKS = {
    "import_module": _check_import_module,
    "python_environment": _check_python_environment,
    "addon_status": _check_addon_status,
    "sleep": _check_sleep,
}


def run_check(command_name: str, args: Optional[Dict[str, Any]] = None, timeout_secs: Optional[float] = 30.0) -> dict:
    """Run a named in-process development check with a timeout envelope."""
    check = _CHECKS.get(command_name)
    if check is None:
        return skill_error(
            f"Unknown check: {command_name}",
            f"Expected one of {sorted(_CHECKS)}.",
            available=sorted(_CHECKS),
        )

    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="blender-dev-check")
    future = executor.submit(check, args or {})
    timed_out = False
    try:
        timeout = 30.0 if timeout_secs is None else max(float(timeout_secs), 0.001)
        return future.result(timeout=timeout)
    except TimeoutError:
        timed_out = True
        future.cancel()
        return skill_error(
            f"Check timed out: {command_name}",
            f"The check exceeded {timeout_secs} seconds.",
            timeout_secs=timeout_secs,
            command_name=command_name,
        )
    except Exception as exc:
        return skill_exception(exc, message=f"Check failed: {command_name}")
    finally:
        executor.shutdown(wait=not timed_out, cancel_futures=True)


def _call_with_args(function: Any, args: Any) -> Any:
    if args is None:
        return function()
    if isinstance(args, dict):
        return function(**args)
    if isinstance(args, list):
        return function(*args)
    return function(args)


def run_entrypoint(module: str, function: str, args: Any = None) -> dict:
    """Import a module and run a named function for reproducible add-on checks."""
    try:
        imported = importlib.import_module(module)
        entrypoint = getattr(imported, function, None)
        if not callable(entrypoint):
            return skill_error(
                f"Entrypoint not callable: {module}.{function}", "The named attribute is missing or not callable."
            )

        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
            result = _call_with_args(entrypoint, args)
        return skill_success(
            f"Ran {module}.{function}",
            module=module,
            function=function,
            stdout=stdout_buf.getvalue(),
            stderr=stderr_buf.getvalue(),
            result=_jsonable(result),
        )
    except Exception as exc:
        return skill_exception(exc, message=f"Failed to run {module}.{function}")


def run_script(path: str, args: Any = None) -> dict:
    """Execute a Python script file inside the Blender process."""
    try:
        script_path = Path(path).expanduser().resolve()
        if not script_path.exists():
            return skill_error(f"Script not found: {path}", f"No file exists at {script_path}.")
        if script_path.suffix != ".py":
            return skill_error(f"Unsupported script file: {path}", "Only Python .py scripts can be run by this tool.")

        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        old_argv = sys.argv[:]
        argv = args if isinstance(args, list) else []
        sys.argv = [str(script_path), *[str(item) for item in argv]]
        try:
            with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
                globals_after = runpy.run_path(str(script_path), init_globals={"ARGS": args}, run_name="__main__")
        finally:
            sys.argv = old_argv

        interesting_globals = {
            key: _jsonable(value)
            for key, value in globals_after.items()
            if not key.startswith("__") and not isinstance(value, ModuleType)
        }
        return skill_success(
            f"Ran script {script_path.name}",
            path=str(script_path),
            stdout=stdout_buf.getvalue(),
            stderr=stderr_buf.getvalue(),
            globals=interesting_globals,
            result=interesting_globals.get("result"),
        )
    except Exception as exc:
        return skill_exception(exc, message=f"Failed to run script {path}")


def _addon_bl_info(module: Any) -> Dict[str, Any]:
    try:
        import addon_utils

        info = addon_utils.module_bl_info(module)
    except Exception:
        info = getattr(module, "bl_info", {}) or {}
    return _jsonable(info) if isinstance(info, dict) else {}


def _addon_modules() -> List[Any]:
    """Return the cached add-on module list.

    addon_utils caches the list, so a call here does not see a module that was
    just installed or removed. Call ``_refresh_addon_modules()`` first when the
    result needs to reflect a change made in this session, otherwise checks such
    as "is it installed" silently answer from stale data.
    """
    import addon_utils

    return list(addon_utils.modules(refresh=False))


def _refresh_addon_modules() -> List[Any]:
    """Rescan the add-on paths and return the refreshed module list."""
    import addon_utils

    return list(addon_utils.modules(refresh=True))


def _validate_addon_module(addon_module: str, mutate: bool = False) -> None:
    # Legacy file/package names can contain hyphens; addon_utils imports them by string.
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*|[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+", addon_module):
        raise ValueError("addon_module must be a legacy module name or dotted Python module identity")
    if mutate and addon_module.rsplit(".", 1)[-1] in {"dcc_mcp_blender", "bpy", "addon_utils"}:
        raise ValueError("The control add-on and Blender infrastructure cannot manage themselves")
    if mutate and threading.current_thread() is not threading.main_thread():
        raise RuntimeError("Add-on changes must run on Blender's main thread")


def _addon_status_data(addon_module: str, operators: Optional[List[str]] = None) -> Dict[str, Any]:
    import addon_utils

    _validate_addon_module(addon_module)
    enabled_default, enabled_runtime = addon_utils.check(addon_module)
    module = next(
        (candidate for candidate in _addon_modules() if getattr(candidate, "__name__", None) == addon_module), None
    )
    info = _addon_bl_info(module) if module is not None else {}
    loaded = sys.modules.get(addon_module)
    origin = getattr(loaded, "__file__", None) or getattr(module, "__file__", None)
    discoverable = loaded is not None or bool(origin and Path(origin).exists())
    discovery_error = None
    try:
        # PathFinder avoids importing parents while inspecting an Extension's dotted identity.
        paths = None
        for index, part in enumerate(addon_module.split(".")):
            name = ".".join(addon_module.split(".")[: index + 1])
            parent = sys.modules.get(name)
            parent_paths = getattr(parent, "__path__", None)
            if parent_paths is not None:
                paths = parent_paths
                if index == len(addon_module.split(".")) - 1:
                    discoverable = True
                continue
            spec = getattr(parent, "__spec__", None) or importlib.machinery.PathFinder.find_spec(name, paths)
            if spec is None:
                break
            discoverable = index == len(addon_module.split(".")) - 1 or discoverable
            if index == len(addon_module.split(".")) - 1:
                origin = origin or spec.origin
            paths = spec.submodule_search_locations
            if paths is None:
                break
    except Exception as exc:
        discovery_error = str(exc)
    operator_states = {}
    if operators:
        if len(operators) > 32:
            raise ValueError("Inspect at most 32 operators")
        import bpy

        for operator in operators:
            if not re.fullmatch(r"[A-Za-z_]\w*\.[A-Za-z_]\w*", operator):
                raise ValueError("Operator identity must be category.name")
            category, name = operator.split(".")
            try:
                getattr(getattr(bpy.ops, category), name).get_rna_type()
                operator_states[operator] = True
            except (AttributeError, KeyError, RuntimeError):
                operator_states[operator] = False
    return {
        "addon_module": addon_module,
        # installed is retained as the legacy catalog-membership alias, not a disk-absence claim.
        "installed": module is not None,
        "catalog_member": module is not None,
        "discoverable": discoverable,
        "loaded": loaded is not None,
        "enabled_default": bool(enabled_default),
        "enabled_runtime": bool(enabled_runtime),
        "enabled": bool(enabled_runtime),
        "name": info.get("name") or addon_module,
        "version": info.get("version"),
        "category": info.get("category"),
        "description": info.get("description"),
        "file": origin,
        "on_disk": bool(origin and Path(origin).exists()),
        "discovery_error": discovery_error,
        "operators": operator_states,
    }


def list_addons(filter: Optional[str] = None, limit: int = 100, offset: int = 0) -> dict:
    """List bounded catalog entries; absent entries may still be Python-discoverable."""
    try:
        if not 1 <= limit <= 500 or offset < 0:
            raise ValueError("limit must be 1..500 and offset must be non-negative")
        query = (filter or "").lower()
        names = []
        for module in _addon_modules():
            module_name = getattr(module, "__name__", "")
            info = _addon_bl_info(module)
            if not query or query in module_name.lower() or query in str(info.get("name", "")).lower():
                names.append(module_name)
        names.sort()
        addons = [_addon_status_data(name) for name in names[offset : offset + limit]]
        return skill_success(
            f"Found {len(addons)} add-ons",
            count=len(addons),
            total=len(names),
            addons=addons,
            filter=filter,
            offset=offset,
            limit=limit,
        )
    except Exception as exc:
        return skill_exception(exc, message="Failed to list add-ons")


def get_addon_status(addon_module: str, operators: Optional[List[str]] = None) -> dict:
    """Inspect catalog, discovery, loaded state, preferences and optional Operators separately."""
    try:
        return skill_success(
            f"Add-on status retrieved for {addon_module}", **_addon_status_data(addon_module, operators)
        )
    except Exception as exc:
        return skill_exception(exc, message=f"Failed to get add-on status for {addon_module}")


def _set_addon_enabled(
    addon_module: str,
    enabled: bool,
    persist: bool = False,
    operators: Optional[List[str]] = None,
) -> dict:
    """Change registration in this host; preferences are written only on explicit request."""
    try:
        _validate_addon_module(addon_module, mutate=True)
        import addon_utils
        import bpy

        before = _addon_status_data(addon_module, operators)
        errors = []

        def handle_error(exc):
            errors.append(str(exc))

        if enabled:
            module = addon_utils.enable(
                addon_module, default_set=persist, persistent=persist, handle_error=handle_error
            )
            if module is None:
                errors.append("Add-on registration returned no module")
        else:
            addon_utils.disable(addon_module, default_set=persist, handle_error=handle_error)
        after = _addon_status_data(addon_module, operators)
        context = dict(before=before, after=after, persistence_requested=persist, preferences_saved=False)
        if errors or after["enabled_runtime"] != enabled or (persist and after["enabled_default"] != enabled):
            return skill_error("Add-on registration did not reach the requested state", "; ".join(errors), **context)
        if persist:
            try:
                result = bpy.ops.wm.save_userpref()
                context["preferences_saved"] = "FINISHED" in result
                context["preferences_operator_result"] = sorted(result)
            except Exception as exc:
                return skill_exception(exc, message="Add-on changed, but preferences could not be saved", **context)
            if not context["preferences_saved"]:
                return skill_error(
                    "Add-on changed, but preferences were not saved", "save_userpref did not finish", **context
                )
        if enabled and operators and not all(after["operators"].values()):
            return skill_error(
                "Add-on registered, but required Operators are unavailable", "Inspect after.operators", **context
            )
        return skill_success(f"{'Enabled' if enabled else 'Disabled'} add-on {addon_module}", **context)
    except Exception as exc:
        # Registration may have partially changed state; return readback whenever possible.
        try:
            after = _addon_status_data(addon_module, operators)
        except Exception:
            after = None
        return skill_exception(exc, message=f"Failed to change add-on {addon_module}", after=after)


def enable_addon(addon_module: str, persist: bool = False, operators: Optional[List[str]] = None) -> dict:
    """Enable in the current process; optionally save default preferences."""
    return _set_addon_enabled(addon_module, True, persist, operators)


def disable_addon(addon_module: str, persist: bool = False) -> dict:
    """Disable in the current process; optionally save default preferences."""
    return _set_addon_enabled(addon_module, False, persist)


def _area_context(screen_name: str, area: Any) -> Dict[str, Any]:
    spaces = _iterable(getattr(area, "spaces", []))
    active_space = getattr(getattr(area, "spaces", None), "active", None)
    if active_space is None and spaces:
        active_space = spaces[0]
    regions = _iterable(getattr(area, "regions", []))
    return {
        "screen": screen_name,
        "area_type": getattr(area, "type", None),
        "region_types": [getattr(region, "type", None) for region in regions],
        "space_types": [getattr(space, "type", None) for space in spaces],
        "active_space_type": getattr(active_space, "type", None) if active_space is not None else None,
    }


def capture_ui_snapshot(area_filter: Optional[str] = None) -> dict:
    """Return structured Blender UI/window/screen metadata."""
    try:
        import bpy

        query = (area_filter or "").lower()
        screens = []
        for screen in _iterable(getattr(bpy.data, "screens", [])):
            screen_name = getattr(screen, "name", "")
            areas = []
            for area in _iterable(getattr(screen, "areas", [])):
                area_type = str(getattr(area, "type", ""))
                if query and query not in area_type.lower() and query not in screen_name.lower():
                    continue
                areas.append(_area_context(screen_name, area))
            if areas or not query:
                screens.append({"name": screen_name, "areas": areas, "area_count": len(areas)})

        windows = []
        window_manager = getattr(bpy.context, "window_manager", None)
        for window in _iterable(getattr(window_manager, "windows", [])):
            screen = getattr(window, "screen", None)
            windows.append({"screen": getattr(screen, "name", None)})

        return skill_success(
            "Captured Blender UI snapshot",
            is_background=bool(getattr(bpy.app, "background", False)),
            window_count=len(windows),
            windows=windows,
            screens=screens,
            screen_count=len(screens),
            area_filter=area_filter,
        )
    except ImportError:
        return skill_error("Blender not available", "bpy could not be imported")
    except Exception as exc:
        return skill_exception(exc, message="Failed to capture UI snapshot")


def find_ui_elements(query: str, area_filter: Optional[str] = None) -> dict:
    """Find structured UI metadata entries matching a query."""
    try:
        if not query:
            return skill_error("Missing UI query", "Pass a non-empty query string.")
        snapshot = capture_ui_snapshot(area_filter=area_filter)
        if not snapshot.get("success"):
            return snapshot

        needle = query.lower()
        matches = []
        for screen in snapshot.get("context", {}).get("screens", []):
            screen_name = str(screen.get("name") or "")
            if needle in screen_name.lower():
                matches.append({"kind": "screen", "screen": screen_name})
            for area in screen.get("areas", []):
                area_type = str(area.get("area_type") or "")
                if needle in area_type.lower():
                    matches.append({"kind": "area", "screen": screen_name, "area_type": area_type})
                for region_type in area.get("region_types", []):
                    if needle in str(region_type).lower():
                        matches.append(
                            {
                                "kind": "region",
                                "screen": screen_name,
                                "area_type": area_type,
                                "region_type": region_type,
                            }
                        )
                for space_type in area.get("space_types", []):
                    if needle in str(space_type).lower():
                        matches.append(
                            {
                                "kind": "space",
                                "screen": screen_name,
                                "area_type": area_type,
                                "space_type": space_type,
                            }
                        )

        return skill_success(
            f"Found {len(matches)} UI metadata matches",
            query=query,
            area_filter=area_filter,
            count=len(matches),
            matches=matches,
        )
    except Exception as exc:
        return skill_exception(exc, message="Failed to find UI elements")


def start_debug_server(host: str = "127.0.0.1", port: int = 5678) -> dict:
    """Start a debugpy listener if debugpy is available."""
    try:
        import debugpy

        if _DEBUG_SERVER.get("listening"):
            return skill_success("Debug server already listening", **_DEBUG_SERVER)

        address: Tuple[str, int] = (host, int(port))
        debugpy.listen(address)
        _DEBUG_SERVER.update({"listening": True, "host": host, "port": int(port), "backend": "debugpy"})
        return skill_success("Debug server listening", **_DEBUG_SERVER)
    except ImportError:
        return skill_error(
            "debugpy unavailable", "Install debugpy in Blender's Python environment to start a debug server."
        )
    except Exception as exc:
        return skill_exception(exc, message="Failed to start debug server")


def get_python_environment(include_sys_path: bool = True) -> dict:
    """Return Blender Python and package environment diagnostics."""
    try:
        import bpy

        blender = {
            "version": getattr(bpy.app, "version_string", None),
            "version_tuple": list(getattr(bpy.app, "version", ())),
            "binary_path": getattr(bpy.app, "binary_path", None),
            "background": bool(getattr(bpy.app, "background", False)),
        }
    except ImportError:
        blender = {"available": False}

    context = {
        "python_version": sys.version,
        "python_executable": sys.executable,
        "prefix": sys.prefix,
        "base_prefix": getattr(sys, "base_prefix", None),
        "platform": platform.platform(),
        "packages": _package_versions(),
        "blender": blender,
    }
    if include_sys_path:
        context["sys_path"] = list(sys.path)
    return skill_success("Python environment retrieved", **context)


def install_addon(
    file_path: Optional[str] = None,
    addon_module: Optional[str] = None,
    overwrite: bool = False,
    enable: bool = True,
    sha256: Optional[str] = None,
    target_directory: Optional[str] = None,
    persist: bool = False,
) -> dict:
    """Install a hash-pinned legacy package through the shared native installer."""
    if not file_path:
        return skill_error("No add-on file supplied", "Use refresh_addons to discover existing packages")
    try:
        from dcc_mcp_blender.extension_packages import install_extension_package

        result = install_extension_package(
            {"archive_path": file_path, "package_id": addon_module, "sha256": sha256, "package_kind": "legacy_addon"},
            enable=enable,
            overwrite=overwrite,
            persist=persist,
            target_directory=target_directory,
        )
        return skill_success("Blender legacy add-on installed", **result)
    except Exception as exc:
        return skill_exception(exc, message="Failed to install add-on", **getattr(exc, "context", {}))


def remove_addon(addon_module: str) -> dict:
    """Disable and uninstall a Blender add-on.

    Blender's own addon_remove operator calls ``context.area.tag_redraw()``,
    which is None under ``blender --background``, so the operator raises an
    AttributeError there. Since background mode is how this adapter normally
    runs, this disables through the operator (which does work headless), then
    removes the module files directly and refreshes the cache. Removal is
    confirmed afterwards, so a silent no-op is not possible.

    Args:
        addon_module: Module name of the installed add-on, for example
            ``io_scene_gltf2``.
    """
    try:
        _validate_addon_module(addon_module, mutate=True)
        import bpy

        before = _addon_status_data(addon_module)
        if before.get("enabled"):
            try:
                bpy.ops.preferences.addon_disable(module=addon_module)
            except Exception as exc:
                return skill_exception(
                    exc,
                    message=f"Failed to disable add-on {addon_module} before removing it",
                    before=before,
                    after=_addon_status_data(addon_module),
                )

        removed_paths = _delete_addon_files(addon_module)

        # Both the operator and the file deletion leave the module cache stale,
        # so the post-condition would otherwise still report it as installed.
        try:
            bpy.ops.preferences.addon_refresh()
        except Exception:
            pass
        try:
            _refresh_addon_modules()
        except Exception:
            pass

        after = _addon_status_data(addon_module)
        if after.get("installed"):
            return skill_error(
                f"Add-on was not removed: {addon_module}",
                "The add-on is still registered after deleting its files. Another "
                "installed copy may exist, or Blender could not write to the add-on "
                "directory.",
                before=before,
                after=after,
                removed_paths=removed_paths,
            )
        return skill_success(
            f"Removed add-on {addon_module}",
            before=before,
            after=after,
            removed_paths=removed_paths,
            prompt="Use list_addons to confirm the add-on is gone.",
        )
    except ImportError:
        return skill_error("Blender not available", "bpy or addon_utils could not be imported")
    except Exception as exc:
        return skill_exception(exc, message=f"Failed to remove add-on {addon_module}")


def _delete_addon_files(addon_module: str) -> List[str]:
    """Delete an add-on's module file or package directory and return the paths.

    Returns an empty list when the module is not on disk, which is not an error:
    an add-on can be built into Blender or installed somewhere else.
    """
    import addon_utils

    removed: List[str] = []
    module = next(
        (candidate for candidate in _addon_modules() if getattr(candidate, "__name__", None) == addon_module), None
    )
    if module is None:
        return removed

    file_path = getattr(module, "__file__", None)
    if not file_path:
        return removed

    path = Path(file_path)
    if path.name == "__init__.py":
        # A package: remove the whole directory.
        target: Path = path.parent
        shutil.rmtree(target, ignore_errors=True)
        removed.append(str(target))
    elif path.exists():
        path.unlink()
        removed.append(str(path))

    if removed:
        addon_utils.modules(refresh=True)
    return removed


def refresh_addons() -> dict:
    """Rescan the add-on paths so newly dropped files become visible.

    Installing from disk or copying a file into an add-on directory leaves
    Blender's cached module list stale until it is refreshed. Returns the
    add-on count before and after so a caller can see what appeared.
    """
    try:
        import bpy

        before_count = len(_addon_modules())
        try:
            bpy.ops.preferences.addon_refresh()
        except Exception as exc:
            return skill_exception(exc, message="Failed to refresh add-ons")
        # The operator updates Blender's state, but addon_utils keeps its own
        # cache; without refresh=True here the after count never changes.
        after_count = len(_refresh_addon_modules())
        return skill_success(
            f"Refreshed add-ons ({before_count} -> {after_count})",
            before_count=before_count,
            after_count=after_count,
            added=max(0, after_count - before_count),
            prompt="Use list_addons to inspect the refreshed list.",
        )
    except ImportError:
        return skill_error("Blender not available", "bpy or addon_utils could not be imported")
    except Exception as exc:
        return skill_exception(exc, message="Failed to refresh add-ons")
