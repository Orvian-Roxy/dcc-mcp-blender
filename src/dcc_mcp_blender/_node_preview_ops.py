"""Render bounded node, property and material-slot trials, restoring each baseline."""

from __future__ import annotations

import json
import math
import re
import time
from pathlib import Path

from dcc_mcp_core.skill import skill_error, skill_success

from . import _node_runtime_ops as nodes
from . import _rna_runtime_ops as rna
from ._viewport_capture import capture_viewport as _capture_viewport


def _identifier(value):
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", value) is None:
        raise ValueError("Target and variant IDs must contain 1..64 filename-safe letters, digits, _, . or -")
    return value


def _parameter(tree, operation):
    if not isinstance(operation, dict) or set(operation) != {"op", "node", "socket", "value"}:
        raise ValueError("Parameter operations require exactly op, node, socket and value")
    if operation["op"] not in {"set_input", "set_output"}:
        raise ValueError("Preview accepts only set_input and set_output parameter operations")
    socket = nodes._socket(
        tree, operation["node"], operation["socket"], "INPUT" if operation["op"] == "set_input" else "OUTPUT"
    )
    if operation["op"] == "set_input" and getattr(socket, "is_linked", False):
        raise ValueError("Linked inputs do not consume default values; target their driving parameter instead")
    if not hasattr(socket, "default_value"):
        raise ValueError("Socket has no editable default value")
    value = operation["value"]
    values = value if isinstance(value, list) else [value]
    if not values or any(type(item) not in (str, bool, int, float) for item in values):
        raise ValueError("Parameters must contain primitive scalar values or arrays")
    if any(type(item) in (int, float) and not math.isfinite(item) for item in values):
        raise ValueError("Numeric parameters must be finite")
    prop = next((item for item in socket.bl_rna.properties if item.identifier == "default_value"), None)
    if prop is not None:
        if prop.type not in {"FLOAT", "INT", "BOOLEAN", "STRING", "ENUM"} or prop.is_readonly:
            raise ValueError("Socket parameter is not an editable primitive")
        nodes._check_value(value, prop)
        length = getattr(prop, "array_length", 0)
        if (length and (not isinstance(value, list) or len(value) != length)) or (
            not length and isinstance(value, list)
        ):
            raise ValueError("Parameter array length does not match the socket schema")


def _rna_target(bpy, spec):
    if set(spec) != {"id", "owner", "path", "base_value"} or spec["owner"].get("type") != "object":
        raise ValueError("RNA targets require id, object owner, path and base_value")
    owner = rna._owner(bpy, spec["owner"])
    tokens = rna._path_tokens(spec["path"])
    light = tokens in [[("attr", "data"), ("attr", name)] for name in ("energy", "color")]
    visibility = tokens in [[("attr", name)] for name in ("hide_render", "hide_viewport")]
    displacement = (
        len(tokens) == 3
        and tokens[0] == ("attr", "modifiers")
        and tokens[1][0] == "key"
        and tokens[2] == ("attr", "strength")
    )
    if not (light or visibility or displacement):
        raise ValueError("Preview RNA supports light energy/color, object visibility and Displace strength only")
    value, parent, prop = rna._resolve(owner, tokens)
    if (light and owner.type != "LIGHT") or (displacement and parent.type != "DISPLACE"):
        raise ValueError("RNA preview owner does not match the requested operation")
    if (
        prop is None
        or prop.is_readonly
        or getattr(prop, "is_runtime", False)
        or prop.type not in {"FLOAT", "INT", "BOOLEAN"}
        or any(
            getattr(item, "library", None) is not None or not getattr(item, "is_editable", True)
            for item in (owner, parent)
        )
    ):
        raise ValueError("RNA preview requires an editable native numeric/boolean property")
    if light and getattr(parent, "users", 1) > 1:
        raise ValueError("Shared Light data cannot be previewed through one object")
    baseline = rna._read_value(value, prop)
    rna._validated_value(prop, spec["base_value"])
    if not nodes._equal_value(baseline, spec["base_value"]):
        raise ValueError("Stale RNA base_value")
    return {"parent": parent, "prop": prop, "baseline": baseline, "spec": spec}


def _rna_write(target, value):
    parent, prop = target["parent"], target["prop"]
    setattr(parent, prop.identifier, rna._validated_value(prop, value))
    actual = rna._read_value(getattr(parent, prop.identifier), prop)
    if not nodes._equal_value(actual, value):
        raise ValueError("RNA value readback differs")


def _material_name(material):
    return material.name if material is not None else None


def _material_target(bpy, spec):
    if (
        set(spec) != {"id", "owner", "material_slot", "base_link", "base_material"}
        or not isinstance(spec["owner"], dict)
        or set(spec["owner"]) != {"type", "name"}
        or spec["owner"]["type"] != "object"
    ):
        raise ValueError("Material targets require id, object owner, material_slot, base_link and base_material")
    owner = rna._owner(bpy, spec["owner"])
    index = spec["material_slot"]
    if owner.type != "MESH" or owner.mode != "OBJECT":
        raise ValueError("Material preview requires a mesh in Object mode")
    if type(index) is not int or not 0 <= index < len(owner.material_slots) or index >= len(owner.data.materials):
        raise ValueError("Material slot index is out of range")
    if any(
        getattr(item, "library", None) is not None or not getattr(item, "is_editable", True)
        for item in (owner, owner.data)
    ):
        raise ValueError("Material preview requires a local editable object and mesh")
    slot = owner.material_slots[index]
    if any(slot.is_property_readonly(name) for name in ("link", "material")):
        raise ValueError("Material slot binding is read-only")
    if spec["base_link"] not in {"DATA", "OBJECT"} or slot.link != spec["base_link"]:
        raise ValueError("Stale material slot base_link")
    baseline = spec["base_material"]
    if (baseline is not None and (not isinstance(baseline, str) or not baseline)) or _material_name(
        slot.material
    ) != baseline:
        raise ValueError("Stale material slot base_material")
    return {
        "slot": slot,
        "owner": owner,
        "material": slot.material,
        "link": slot.link,
        "data_material": owner.data.materials[index],
        "spec": spec,
        "replacements": {},
    }


def _material_operation(bpy, target, operation):
    if (
        not isinstance(operation, dict)
        or set(operation) != {"op", "material"}
        or operation["op"] != "set_material"
        or not isinstance(operation["material"], str)
        or not operation["material"]
    ):
        raise ValueError("Material preview accepts set_material with an existing material name only")
    material = bpy.data.materials.get(operation["material"])
    if material is None:
        raise ValueError("Preview material not found: " + operation["material"])
    target["replacements"][operation["material"]] = material


def _material_write(target, material, undo):
    slot, state = target["slot"], {}

    def restore():
        try:
            if "override" in state:
                slot.link = "OBJECT"
                if slot.link != "OBJECT":
                    raise ValueError("Material slot OBJECT binding readback differs")
                slot.material = state["override"]
                if slot.material != state["override"]:
                    raise ValueError("Dormant object material restoration differs")
        finally:
            slot.link = target["link"]

    undo.append(restore)
    slot.link = "OBJECT"
    if slot.link != "OBJECT":
        raise ValueError("Material slot OBJECT binding readback differs")
    # DATA slots can retain a dormant object override; restore it before the link.
    state["override"] = slot.material
    slot.material = material
    if slot.material != material:
        raise ValueError("Material slot assignment readback differs")


def _link_state(tree):
    return sorted(
        (
            link.from_node.name,
            link.from_socket.identifier,
            link.to_node.name,
            link.to_socket.identifier,
            bool(getattr(link, "is_muted", False)),
            bool(getattr(link, "is_valid", True)),
        )
        for link in tree.links
    )


def _link_operation(tree, operation, links):
    if set(operation) != {"op", "from_node", "from_socket", "to_node", "to_socket"}:
        raise ValueError("Link operations require exact source and destination sockets")
    source = nodes._socket(tree, operation["from_node"], operation["from_socket"], "OUTPUT")
    target = nodes._socket(tree, operation["to_node"], operation["to_socket"], "INPUT")
    if getattr(target, "is_multi_input", False):
        raise ValueError("Multi-input links are not supported")
    if operation["op"] == "disconnect":
        matches = [i for i, (src, dst, muted) in enumerate(links) if src == source and dst == target]
        if len(matches) != 1 or links[matches[0]][2]:
            raise ValueError("Preview disconnect requires one unmuted exact link")
        del links[matches[0]]
    else:
        if any(dst == target for _, dst, _ in links):
            raise ValueError("Disconnect the occupied input before connecting")
        limit = getattr(source, "link_limit", 0)
        if limit > 0 and sum(src == source for src, _, _ in links) >= limit:
            raise ValueError("Source link limit would remove another consumer")
        links.append((source, target, False))


def _preflight(bpy, targets, variants, output_directory, resolution_x, resolution_y):
    if not isinstance(targets, list) or not 1 <= len(targets) <= 16:
        raise ValueError("targets must contain 1..16 target objects")
    if not isinstance(variants, list) or not 1 <= len(variants) <= 16:
        raise ValueError("variants must contain 1..16 candidate objects")
    for dimension in (resolution_x, resolution_y):
        if type(dimension) is not int or not 16 <= dimension <= 4096:
            raise ValueError("Preview dimensions must be integers from 16 to 4096")
    if not isinstance(output_directory, str) or not Path(output_directory).is_absolute():
        raise ValueError("output_directory must be an absolute path")
    directory = Path(output_directory).resolve()
    if directory.exists() and not directory.is_dir():
        raise ValueError("output_directory is not a directory")
    resolved, tree_ids = {}, set()
    for target in targets:
        if isinstance(target, dict) and "material_slot" in target:
            target_id = _identifier(target.get("id"))
            item = _material_target(bpy, target)
            owner = item["owner"]
            pointer = int(owner.as_pointer()) if callable(getattr(owner, "as_pointer", None)) else id(owner)
            identity = ("material_slot", pointer, target["material_slot"])
            if target_id in resolved or identity in tree_ids:
                raise ValueError("Duplicate target ID or material slot")
            resolved[target_id] = item
            tree_ids.add(identity)
            continue
        if isinstance(target, dict) and "owner" in target:
            target_id = _identifier(target.get("id"))
            item = _rna_target(bpy, target)
            parent = item["parent"]
            pointer = int(parent.as_pointer()) if callable(getattr(parent, "as_pointer", None)) else id(parent)
            identity = (pointer, item["prop"].identifier)
            if target_id in resolved or identity in tree_ids:
                raise ValueError("Duplicate target ID or RNA property")
            resolved[target_id] = item
            tree_ids.add(identity)
            continue
        if (
            not isinstance(target, dict)
            or not {"id", "tree_ref", "base_revision"} <= set(target)
            or set(target) - {"id", "tree_ref", "base_revision", "allow_shared"}
        ):
            raise ValueError("Targets require id, tree_ref and base_revision; allow_shared is optional")
        target_id = _identifier(target["id"])
        owner, tree = nodes._resolve(bpy, target["tree_ref"])
        pointer = int(tree.as_pointer()) if callable(getattr(tree, "as_pointer", None)) else id(tree)
        if target_id in resolved or pointer in tree_ids:
            raise ValueError("Duplicate target ID or node tree")
        if tree.bl_idname not in {"ShaderNodeTree", "CompositorNodeTree"}:
            raise ValueError("Preview supports Shader and Compositor trees only")
        if any(
            getattr(item, "library", None) is not None or not getattr(item, "is_editable", True)
            for item in (owner, tree)
        ):
            raise ValueError("Linked or non-editable node trees cannot be previewed")
        allow_shared = target.get("allow_shared", False)
        if type(allow_shared) is not bool:
            raise ValueError("allow_shared must be a boolean")
        if tree.users > 1 and not allow_shared:
            raise ValueError("Shared trees require allow_shared=true")
        revision = nodes._revision(tree, require_complete=True)
        if revision != target["base_revision"]:
            raise ValueError("Stale node revision for target " + target_id)
        resolved[target_id] = {"tree": tree, "spec": target, "revision": revision}
        tree_ids.add(pointer)
    candidate_ids = set()
    paths = [directory / "preview-manifest.json"]
    for candidate in variants:
        if not isinstance(candidate, dict) or set(candidate) != {"id", "patches"}:
            raise ValueError("Each variant requires exactly id and patches")
        candidate_id = _identifier(candidate["id"])
        if candidate_id in candidate_ids:
            raise ValueError("Duplicate variant ID")
        candidate_ids.add(candidate_id)
        patches = candidate["patches"]
        if not isinstance(patches, list) or not 0 <= len(patches) <= len(resolved):
            raise ValueError("Variant patches must name 0..N unique targets")
        patched, operation_count = set(), 0
        for patch in patches:
            if not isinstance(patch, dict) or set(patch) != {"target", "operations"}:
                raise ValueError("Patches require exactly target and operations")
            target_id = patch["target"]
            if not isinstance(target_id, str) or target_id not in resolved or target_id in patched:
                raise ValueError("Unknown or duplicate patch target")
            patched.add(target_id)
            operations = patch["operations"]
            if not isinstance(operations, list) or not 1 <= len(operations) <= 100:
                raise ValueError("Each patch requires 1..100 parameter operations")
            operation_count += len(operations)
            item = resolved[target_id]
            if "slot" in item and len(operations) != 1:
                raise ValueError("A material slot patch requires exactly one set_material operation")
            links = (
                [
                    (link.from_socket, link.to_socket, bool(getattr(link, "is_muted", False)))
                    for link in item["tree"].links
                ]
                if "tree" in item
                else []
            )
            for operation in operations:
                if "slot" in item:
                    _material_operation(bpy, item, operation)
                elif "parent" in item:
                    if (
                        not isinstance(operation, dict)
                        or set(operation) != {"op", "value"}
                        or operation["op"] != "set_property"
                    ):
                        raise ValueError("RNA preview accepts set_property with value only")
                    rna._validated_value(item["prop"], operation["value"])
                elif isinstance(operation, dict) and operation.get("op") in {"connect", "disconnect"}:
                    _link_operation(item["tree"], operation, links)
                    item["link_state"] = _link_state(item["tree"])
                else:
                    _parameter(item["tree"], operation)
        if operation_count > 100:
            raise ValueError("A variant may contain at most 100 parameter operations")
        paths.append(directory / (candidate_id + ".png"))
    if any(path.exists() or path.is_symlink() for path in paths):
        raise ValueError("Preview output already exists; choose a new output directory or candidate IDs")
    return directory, resolved


def _restored_targets(resolved):
    targets, errors = [], []
    for target_id, target in resolved.items():
        try:
            if "slot" in target:
                slot = target["slot"]
                targets.append(
                    {**target["spec"], "base_link": slot.link, "base_material": _material_name(slot.material)}
                )
                if (
                    slot.link != target["link"]
                    or slot.material != target["material"]
                    or target["owner"].data.materials[target["spec"]["material_slot"]] != target["data_material"]
                ):
                    errors.append("Baseline material binding differs for target " + target_id)
                continue
            if "parent" in target:
                actual = rna._read_value(getattr(target["parent"], target["prop"].identifier), target["prop"])
                targets.append({**target["spec"], "base_value": actual})
                if not nodes._equal_value(actual, target["baseline"]):
                    errors.append("Baseline RNA value differs for target " + target_id)
                continue
            if "link_state" in target and _link_state(target["tree"]) != target["link_state"]:
                errors.append("Baseline link state differs for target " + target_id)
            revision = nodes._revision(target["tree"], require_complete=True)
            targets.append(
                {
                    "id": target_id,
                    "tree_ref": target["spec"]["tree_ref"],
                    "revision": revision,
                    "allow_shared": target["spec"].get("allow_shared", False),
                }
            )
            if revision != target["revision"]:
                errors.append("Baseline revision differs for target " + target_id)
        except Exception as exc:
            errors.append(target_id + ": " + str(exc))
    return targets, errors


def preview_variants(targets, variants, output_directory, resolution_x=1200, resolution_y=1000):
    """Capture independent candidates; return patches and actual restoration evidence."""
    started = time.perf_counter()
    try:
        bpy = nodes._host()
        directory, resolved = _preflight(bpy, targets, variants, output_directory, resolution_x, resolution_y)
    except Exception as exc:
        return skill_error(
            "Parameter preview rejected", str(exc), failure_stage="preflight", results=[], mutated=False, restored=True
        )
    dirty_before = bool(bpy.data.is_dirty)
    results, restore_errors, target_results = [], [], []
    failure_stage, detail = None, None
    try:
        directory.mkdir(parents=True, exist_ok=True)
        for candidate in variants:
            record = {"id": candidate["id"], "patches": candidate["patches"], "images": [], "status": "running"}
            results.append(record)
            undo = []
            case_started = time.perf_counter()
            stage = "apply"
            try:
                for patch in candidate["patches"]:
                    target = resolved[patch["target"]]
                    if "slot" in target:
                        _material_write(target, target["replacements"][patch["operations"][0]["material"]], undo)
                    elif "parent" in target:
                        undo.append(lambda target=target: _rna_write(target, target["baseline"]))
                        for operation in patch["operations"]:
                            _rna_write(target, operation["value"])
                    else:
                        nodes._execute(target["tree"], patch["operations"], undo)
                record["apply_seconds"] = time.perf_counter() - case_started
                stage = "capture"
                capture_started = time.perf_counter()
                path = directory / (candidate["id"] + ".png")
                if path.exists() or path.is_symlink():
                    raise RuntimeError("Preview destination appeared after preflight; refusing to overwrite")
                capture = _capture_viewport(filepath=str(path), resolution_x=resolution_x, resolution_y=resolution_y)
                if not capture.get("success"):
                    record["capture_error"] = capture
                    raise RuntimeError("Viewport capture failed")
                info = capture.get("context", {})
                record["images"].append(
                    {
                        "path": str(path),
                        "view": "current",
                        "width": info.get("width"),
                        "height": info.get("height"),
                        "bytes": path.stat().st_size,
                    }
                )
                record["capture_seconds"] = time.perf_counter() - capture_started
                record["status"] = "complete"
            except Exception as exc:
                failure_stage, detail = stage, str(exc)
                record.update(status="failed", failure_stage=stage, detail=detail)
            finally:
                restore_started = time.perf_counter()
                for restore in reversed(undo):
                    try:
                        restore()
                    except Exception as exc:
                        restore_errors.append(str(exc))
                target_results, revision_errors = _restored_targets(resolved)
                restore_errors.extend(revision_errors)
                record["restore_seconds"] = time.perf_counter() - restore_started
                record["elapsed_seconds"] = time.perf_counter() - case_started
                record["restored"] = not restore_errors
            if restore_errors:
                failure_stage, detail = "restore", "Candidate baseline restoration failed"
                record.update(status="failed", failure_stage="restore", detail=detail)
            if failure_stage:
                break
    except Exception as exc:
        failure_stage, detail = "output", str(exc)
    restored = not restore_errors
    context = {
        "results": results,
        "targets": target_results,
        "restored": restored,
        "mutated": not restored,
        "restore_errors": restore_errors,
        "dirty_before": dirty_before,
        "dirty_after": bool(bpy.data.is_dirty),
        "elapsed_seconds": time.perf_counter() - started,
    }
    if failure_stage:
        context.update(failure_stage=failure_stage, detail=detail)
    manifest = directory / "preview-manifest.json"
    try:
        with manifest.open("x", encoding="utf-8") as stream:
            json.dump({"success": failure_stage is None, **context}, stream, indent=2, allow_nan=False)
        context["manifest_path"] = str(manifest)
    except Exception as exc:
        if failure_stage is None:
            failure_stage = "manifest"
            context.update(failure_stage=failure_stage, detail=str(exc))
        else:
            context["manifest_error"] = str(exc)
    if failure_stage:
        return skill_error("Parameter preview failed", context.get("detail", failure_stage), **context)
    return skill_success("Parameter previews captured; baseline restored", **context)
