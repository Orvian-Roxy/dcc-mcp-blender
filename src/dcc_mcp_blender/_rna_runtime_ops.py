"""Live Operator calls and bounded RNA owner access. No evaluated Python paths."""

from __future__ import annotations

import ast
import math
import threading
from contextlib import nullcontext
from typing import Any

from dcc_mcp_core.skill import skill_error, skill_exception, skill_success

from ._rna_query import (
    _bounded_page,
    _byte_length,
    _context,
    _identifier,
    _label_fields,
    _page_error,
    _pagination,
    _property_metadata,
    _scalar_value,
)

# File replacement and add-on lifecycle belong to their protected typed tools.
_FILE_OPERATORS = {
    "open_mainfile",
    "save_mainfile",
    "save_as_mainfile",
    "save_homefile",
    "read_homefile",
    "read_factory_settings",
    "read_factory_userpref",
    "read_userpref",
    "save_userpref",
    "revert_mainfile",
    "recover_last_session",
    "recover_auto_save",
    "quit_blender",
    "window_close",
    "addon_install",
    "addon_enable",
    "addon_disable",
    "addon_remove",
}


def _host():
    if threading.current_thread() is not threading.main_thread():
        raise ValueError("RNA access must execute on Blender's main thread.")
    import bpy

    return bpy


def _operator(bpy, operator_id):
    if not isinstance(operator_id, str) or len(operator_id) > 257:
        raise ValueError("operator_id must be namespace.name.")
    parts = operator_id.split(".")
    if len(parts) != 2 or not all(_identifier(part) for part in parts):
        raise ValueError("operator_id must contain two public identifiers.")
    op = getattr(getattr(bpy.ops, parts[0]), parts[1])
    return op, op.get_rna_type()


def _validated_value(prop, value):
    if _byte_length(value) > 8192:
        raise ValueError("A property value may encode at most 8192 bytes.")
    if prop.type not in {"BOOLEAN", "INT", "FLOAT", "STRING", "ENUM"}:
        raise ValueError(f"{prop.identifier}: POINTER/COLLECTION and complex values are unsupported.")
    if getattr(prop, "is_array", False):
        length = getattr(prop, "array_length", 0)
        if not isinstance(value, list) or not 0 < length <= 32 or len(value) != length:
            raise ValueError(f"{prop.identifier}: expected an array of {length} values, at most 32.")
        return [_validated_scalar(prop, item) for item in value]
    if prop.type == "ENUM" and getattr(prop, "is_enum_flag", False):
        if not isinstance(value, list) or len(value) > 32 or len(set(value)) != len(value):
            raise ValueError(f"{prop.identifier}: expected up to 32 unique enum strings.")
        return {_validated_scalar(prop, item) for item in value}
    return _validated_scalar(prop, value)


def _validated_scalar(prop, value):
    result = _scalar_value(value, prop.type)
    if prop.type in {"INT", "FLOAT"}:
        if not getattr(prop, "hard_min", -math.inf) <= value <= getattr(prop, "hard_max", math.inf):
            raise ValueError(f"{prop.identifier}: value outside live RNA range.")
    if prop.type == "ENUM":
        items = getattr(prop, "enum_items_static", ())
        identifiers = {item.identifier for item in items}
        # Dynamic enums can have no static choices; Blender performs final validation.
        if identifiers and result not in identifiers:
            raise ValueError(f"{prop.identifier}: value is not a live enum choice.")
    return result


def _snapshot(bpy):
    context = bpy.context
    active = context.active_object
    area = context.area
    return {
        "active_object": active.name if active else None,
        "selected_objects": sorted(obj.name for obj in context.selected_objects),
        "mode": context.mode,
        "area_type": area.type if area else None,
    }


def _override(bpy, requested):
    if not isinstance(requested, dict) or set(requested) - {"active_object", "selected_objects", "mode", "area_type"}:
        raise ValueError("context accepts active_object, selected_objects, mode and area_type only.")
    actual = _snapshot(bpy)
    for key in ("active_object", "selected_objects", "mode"):
        if key not in requested:
            continue
        expected = requested[key]
        if key == "selected_objects":
            if not isinstance(expected, list) or len(expected) > 100 or any(not isinstance(x, str) for x in expected):
                raise ValueError("selected_objects must contain at most 100 object names.")
            expected = sorted(expected)
        elif expected is not None and not isinstance(expected, str):
            raise ValueError(f"{key} must be a string or null.")
        if expected != actual[key]:
            raise ValueError(f"Existing {key} does not match the requested context: {actual[key]!r}.")
    area_type = requested.get("area_type")
    if area_type is None:
        return nullcontext()
    if not _identifier(area_type):
        raise ValueError("area_type must be an existing editor identifier.")
    window = bpy.context.window
    if window is None:
        raise ValueError(f"No current window for requested editor {area_type}.")
    for area in window.screen.areas:
        if area.type == area_type:
            region = next((region for region in area.regions if region.type == "WINDOW"), None)
            if region is not None:
                return bpy.context.temp_override(window=window, area=area, region=region)
    raise ValueError(f"No existing {area_type} editor with a WINDOW region; user layout was not changed.")


def call_operator(operator_id: str, parameters: dict | None = None, context: dict | None = None) -> dict:
    """Validate every supplied parameter, poll in existing context, then EXEC_DEFAULT once."""
    try:
        bpy = _host()
        op, rna = _operator(bpy, operator_id)
        namespace, name = operator_id.split(".")
        if namespace in {"preferences", "extensions", "script"} or (namespace == "wm" and name in _FILE_OPERATORS):
            raise ValueError("Use existing scene/file or add-on lifecycle typed tools for this operation.")
        supplied = {} if parameters is None else parameters
        if not isinstance(supplied, dict) or len(supplied) > 64:
            raise ValueError("parameters must be an object containing at most 64 fields.")
        validated = {}
        for key, value in supplied.items():
            prop = rna.properties.get(key) if _identifier(key) and key != "rna_type" else None
            if prop is None:
                raise ValueError(f"Unknown Operator field: {key}.")
            if getattr(prop, "is_readonly", False):
                raise ValueError(f"Operator field is read-only: {key}.")
            validated[key] = _validated_value(prop, value)
        with _override(bpy, {} if context is None else context):
            snapshot = _snapshot(bpy)
            if not op.poll():
                return skill_error(
                    "Operator poll failed",
                    "Select the required object, mode or existing editor.",
                    context_snapshot=snapshot,
                )
            status = sorted(op("EXEC_DEFAULT", **validated))
        details = dict(
            operator_id=operator_id,
            operator_status=status,
            context_snapshot=snapshot,
            completed=status == ["FINISHED"],
            business_result_verified=False,
        )
        if status == ["FINISHED"]:
            return skill_success("Operator finished; verify the business result separately", **details)
        return skill_error(
            "Operator did not finish", "Do not automatically retry CANCELLED or RUNNING_MODAL.", **details
        )
    except Exception as exc:
        return skill_exception(exc, message="Operator call rejected or failed")


def search_operators(query: str = "", offset: int = 0, limit: int = 20) -> dict:
    """Search the current Operator registry, including unlabeled plugin operators."""
    error = _page_error(query, offset, limit)
    if error:
        return skill_error("Invalid Operator search", error)
    try:
        bpy = _host()
        matches = []
        needle = query.casefold()
        for namespace in sorted(dir(bpy.ops)):
            if not _identifier(namespace):
                continue
            for name in sorted(dir(getattr(bpy.ops, namespace))):
                if not _identifier(name):
                    continue
                operator_id = f"{namespace}.{name}"
                try:
                    _, rna = _operator(bpy, operator_id)
                except (AttributeError, RuntimeError):
                    continue
                row = {"operator_id": operator_id, **_label_fields(rna), "source_module": None}
                if not needle or any(needle in row[key].casefold() for key in ("operator_id", "name", "description")):
                    matches.append(row)
        page = _bounded_page(matches[offset : offset + limit])
        return skill_success(
            "Live Operator search completed",
            **_context(bpy),
            operators=page,
            **_pagination(len(matches), offset, limit, len(page)),
        )
    except Exception as exc:
        return skill_exception(exc, message="Operator search unavailable")


def describe_operator(operator_id: str, query: str = "", offset: int = 0, limit: int = 20) -> dict:
    """Describe a live Operator's actual RNA parameters, not a generated catalog."""
    error = _page_error(query, offset, limit)
    if error:
        return skill_error("Invalid Operator description", error)
    try:
        bpy = _host()
        _, rna = _operator(bpy, operator_id)
        properties = [
            prop
            for prop in rna.properties
            if _identifier(prop.identifier)
            and prop.identifier != "rna_type"
            and (
                query.casefold() in prop.identifier.casefold()
                or query.casefold() in getattr(prop, "name", "").casefold()
            )
        ]
        page = _bounded_page(_property_metadata(prop) for prop in properties[offset : offset + limit])
        return skill_success(
            "Live Operator described",
            **_context(bpy),
            operator_id=operator_id,
            **_label_fields(rna),
            source_module=None,
            properties=page,
            **_pagination(len(properties), offset, limit, len(page)),
        )
    except Exception as exc:
        return skill_exception(exc, message="Operator description unavailable")


def _owner(bpy, owner):
    if not isinstance(owner, dict) or set(owner) - {"type", "name", "object_name"}:
        raise ValueError("owner accepts type, name and object_name only.")
    kind = owner.get("type")
    name = owner.get("name")
    if kind == "scene" and name is None:
        return bpy.context.scene
    if not isinstance(name, str) or not 1 <= len(name) <= 256:
        raise ValueError("This owner requires an exact name of at most 256 characters.")
    if kind in {"scene", "object", "material"}:
        collection = getattr(bpy.data, {"scene": "scenes", "object": "objects", "material": "materials"}[kind])
        result = collection.get(name)
    elif kind == "pose_bone":
        object_name = owner.get("object_name")
        if not isinstance(object_name, str) or not 1 <= len(object_name) <= 256:
            raise ValueError("pose_bone requires object_name.")
        obj = bpy.data.objects.get(object_name)
        result = obj.pose.bones.get(name) if obj and obj.pose else None
    elif kind == "addon_preferences":
        addon = bpy.context.preferences.addons.get(name)
        result = addon.preferences if addon else None
    else:
        raise ValueError("owner.type must be scene, object, pose_bone, material or addon_preferences.")
    if result is None:
        raise ValueError("Requested owner is not available in this Blender instance.")
    return result


def _path_tokens(path):
    if not isinstance(path, str) or len(path) > 1024:
        raise ValueError("path must contain at most 1024 characters.")
    if not path:
        return []
    expression = ast.parse("owner." + path, mode="eval").body
    tokens = []
    while not isinstance(expression, ast.Name):
        if isinstance(expression, ast.Attribute) and _identifier(expression.attr):
            tokens.append(("attr", expression.attr))
            expression = expression.value
        elif isinstance(expression, ast.Subscript) and isinstance(expression.slice, ast.Constant):
            key = expression.slice.value
            if not ((isinstance(key, str) and 0 < len(key) <= 256) or (type(key) is int and 0 <= key <= 100000)):
                raise ValueError("Collection keys must be short names or nonnegative integer indices.")
            tokens.append(("key", key))
            expression = expression.value
        else:
            raise ValueError("path permits public RNA attributes and literal collection keys only.")
        if len(tokens) > 16:
            raise ValueError("path may contain at most 16 access steps.")
    if expression.id != "owner":
        raise ValueError("Invalid owner path.")
    return list(reversed(tokens))


def _resolve(root, tokens):
    value = root
    prop = None
    parent = None
    for kind, key in tokens:
        if kind == "attr":
            rna = getattr(value, "bl_rna", None)
            prop = rna.properties.get(key) if rna and key != "rna_type" else None
            if prop is None:
                raise ValueError(f"Not a registered RNA property: {key}.")
            parent = value
            value = getattr(value, key)
        else:
            if prop is None or prop.type != "COLLECTION":
                raise ValueError("Literal keys may only index RNA collections.")
            value = value[key]
            prop = None
            parent = None
    return value, parent, prop


def _read_value(value, prop):
    if prop.type not in {"BOOLEAN", "INT", "FLOAT", "STRING", "ENUM"}:
        raise ValueError("Complex RNA values require an explicit owner/group path.")
    if getattr(prop, "is_array", False) and not 0 < getattr(prop, "array_length", 0) <= 32:
        raise ValueError("RNA array length exceeds the supported bound.")
    if prop.type == "ENUM" and getattr(prop, "is_enum_flag", False):
        value = sorted(value)
    elif getattr(prop, "is_array", False):
        value = list(value)
    result = _validated_value(prop, value)
    return sorted(result) if isinstance(result, set) else result


def get_properties(owner: dict, path: str = "", query: str = "", offset: int = 0, limit: int = 20) -> dict:
    """Read one scalar or a page of properties from an explicit RNA owner/group."""
    error = _page_error(query, offset, limit)
    if error:
        return skill_error("Invalid property query", error)
    try:
        bpy = _host()
        value, parent, prop = _resolve(_owner(bpy, owner), _path_tokens(path))
        if prop is not None and prop.type not in {"POINTER", "COLLECTION"}:
            return skill_success("RNA property read", owner=owner, path=path, value=_read_value(value, prop))
        rna = getattr(value, "bl_rna", None)
        if rna is None:
            raise ValueError("Select an RNA owner or PropertyGroup; collection members need an explicit key.")
        needle = query.casefold()
        properties = [
            item
            for item in rna.properties
            if _identifier(item.identifier)
            and item.identifier != "rna_type"
            and (needle in item.identifier.casefold() or needle in getattr(item, "name", "").casefold())
        ]
        rows = []
        for item in properties[offset : offset + limit]:
            row = _property_metadata(item)
            # Runtime properties are deliberate here: plugin settings often have callbacks.
            try:
                if item.type not in {"BOOLEAN", "INT", "FLOAT", "STRING", "ENUM"}:
                    raise ValueError("Expand complex properties with an explicit path.")
                row.update(
                    value=_read_value(getattr(value, item.identifier), item),
                    value_status="available",
                    value_support="supported",
                )
            except (ValueError, TypeError, AttributeError, RuntimeError):
                row.update(value_status="unsupported_or_unavailable")
            rows.append(row)
        page = _bounded_page(rows)
        return skill_success(
            "RNA properties inspected",
            **_context(bpy),
            owner=owner,
            path=path,
            properties=page,
            **_pagination(len(properties), offset, limit, len(page)),
        )
    except Exception as exc:
        return skill_exception(exc, message="RNA property inspection rejected or failed")


def set_property(owner: dict, path: str, value: Any) -> dict:
    """Write a single bounded RNA scalar/array and return its actual readback."""
    try:
        bpy = _host()
        tokens = _path_tokens(path)
        if not tokens or tokens[-1][0] != "attr":
            raise ValueError("A write path must end in one RNA property identifier.")
        _, parent, prop = _resolve(_owner(bpy, owner), tokens)
        if prop is None or getattr(prop, "is_readonly", False):
            raise ValueError("The selected RNA property is not writable.")
        validated = _validated_value(prop, value)
        setattr(parent, prop.identifier, validated)
        actual = _read_value(getattr(parent, prop.identifier), prop)
        return skill_success("RNA property written and read back", owner=owner, path=path, value=actual)
    except Exception as exc:
        return skill_exception(exc, message="RNA property write rejected or failed")
