"""Bounded node inspection and reversible edits on one explicit owner.

RNA property schema adapted from newo-ether/blender-mcp at ced81a5a,
blender_extension/nodes/schema.py (_gn_property_schema), under MIT.
See skills/blender-node-runtime/THIRD_PARTY_LICENSE.txt.
"""

from __future__ import annotations

import hashlib
import json
import math
import threading

from dcc_mcp_core.skill import skill_error, skill_success

from ._node_graph_ops import _link_info, _node_info, _socket_info

_COLLECTIONS = {
    "MATERIAL": "materials",
    "WORLD": "worlds",
    "LIGHT": "lights",
    "SCENE": "scenes",
    "NODE_GROUP": "node_groups",
}
_TYPES = {
    "ShaderNodeTree": {"MATERIAL", "WORLD", "LIGHT", "NODE_GROUP"},
    "CompositorNodeTree": {"SCENE", "NODE_GROUP"},
    "GeometryNodeTree": {"NODE_GROUP"},
}


def _host():
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("Node operations require Blender's main thread")
    import bpy

    return bpy


def _resolve(bpy, tree_ref):
    if not isinstance(tree_ref, dict) or not isinstance(tree_ref.get("owner"), dict):
        raise ValueError("tree_ref requires tree_type and owner {kind, name}")
    tree_type = tree_ref.get("tree_type")
    kind, name = tree_ref["owner"].get("kind"), tree_ref["owner"].get("name")
    if kind not in _TYPES.get(tree_type, set()) or not isinstance(name, str) or not name:
        raise ValueError("Unsupported tree_type/owner or empty owner name")
    owner = getattr(bpy.data, _COLLECTIONS[kind]).get(name)
    if owner is None:
        raise ValueError("Node owner not found")
    if kind == "NODE_GROUP":
        tree = owner
    elif kind == "SCENE" and hasattr(owner, "compositing_node_group"):
        tree = owner.compositing_node_group
    else:
        tree = getattr(owner, "node_tree", None)
    if tree is None or tree.bl_idname != tree_type:
        raise ValueError("Owner has no node tree of the requested type")
    return owner, tree


def _page(offset, limit):
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("offset must be nonnegative; limit must be 1..100")


def _value(value):
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, set):
        return sorted(value)
    try:
        return list(value)
    except TypeError:
        return {"id": getattr(value, "name", None), "type": type(value).__name__}


def _id_identity(value):
    pointer = getattr(value, "as_pointer", None)
    return {
        "name": getattr(value, "name_full", getattr(value, "name", None)),
        "type": type(value).__name__,
        "library": getattr(getattr(value, "library", None), "filepath", None),
        "session_uid": getattr(value, "session_uid", None),
        "pointer": int(pointer()) if callable(pointer) else None,
    }


def _primitive_data(value):
    return {
        prop.identifier: _value(getattr(value, prop.identifier))
        for prop in getattr(getattr(value, "bl_rna", None), "properties", [])
        if prop.identifier != "rna_type"
        and not prop.is_readonly
        and prop.type in {"BOOLEAN", "INT", "FLOAT", "STRING", "ENUM"}
    }


def _stored_node_data(name, value, strict):
    """Explicit Blender node storage, not arbitrary recursive RNA traversal."""
    if value is None:
        return None
    if getattr(value, "id_data", None) is value:
        return _id_identity(value)
    if name == "panel_states":
        if len(value) > 256:
            raise ValueError("Node panel states exceed the 256-item revision bound")
        return [{"identifier": item.identifier, "is_collapsed": item.is_collapsed} for item in value]
    if name == "parent":
        return {"node": value.name}
    if name == "paired_output" and getattr(value, "bl_idname", None) == "NodeClosureOutput":
        return {"node": value.name, "bl_idname": value.bl_idname}
    if name == "color_ramp":
        if len(value.elements) > 256:
            raise ValueError("ColorRamp exceeds the 256-element revision bound")
        return {
            "color_mode": value.color_mode,
            "hue_interpolation": value.hue_interpolation,
            "interpolation": value.interpolation,
            "elements": [{"position": item.position, "color": list(item.color)} for item in value.elements],
        }
    if name == "mapping":
        if len(value.curves) > 8 or any(len(curve.points) > 256 for curve in value.curves):
            raise ValueError("CurveMapping exceeds the 8-curve/256-point revision bound")
        return {
            "settings": _primitive_data(value),
            "curves": [
                [{"location": list(point.location), "handle_type": point.handle_type} for point in curve.points]
                for curve in value.curves
            ],
        }
    if name in {"image_user", "texture_mapping", "color_mapping"}:
        result = _primitive_data(value)
        if name == "color_mapping" and hasattr(value, "color_ramp"):
            result["color_ramp"] = _stored_node_data("color_ramp", value.color_ramp, strict)
        return result
    if name == "enum_definition":
        if len(value.enum_items) > 256:
            raise ValueError("Node enum exceeds the 256-item revision bound")
        return [_primitive_data(item) for item in value.enum_items]
    if name in {"input_items", "output_items"}:
        if len(value) > 256:
            raise ValueError("Closure node items exceed the 256-item revision bound")
        item_types = (
            {"NodeClosureInputItem", "NodeEvaluateClosureInputItem"}
            if name == "input_items"
            else {"NodeClosureOutputItem", "NodeEvaluateClosureOutputItem"}
        )
        if all(getattr(getattr(item, "bl_rna", None), "identifier", None) in item_types for item in value):
            return [
                {"name": item.name, "socket_type": item.socket_type, "structure_type": item.structure_type}
                for item in value
            ]
    if name == "active_item":
        return {"name": value.name, "description": value.description}
    if name == "define_values" and len(value) == 0:
        # NPR GLSLFunction evidence qualifies the empty collection only.
        return []
    if name in {
        "index_switch_items",
        "repeat_items",
        "state_items",
        "capture_items",
        "bake_items",
        "menu_items",
        "enum_items",
    }:
        if len(value) > 256:
            raise ValueError("Dynamic node items exceed the 256-item revision bound")
        return [_primitive_data(item) for item in value]
    if strict:
        raise ValueError(f"Node storage {name!r} has no reliable revision support; patch refused")
    return {"revision_unqualified": name}


def _revision(tree, require_complete=False, _depth=0, _budget=None, _memo=None):
    if _memo is None:
        _memo = {}
    key = int(tree.as_pointer()) if callable(getattr(tree, "as_pointer", None)) else id(tree)
    if key in _memo:
        if _memo[key] is None:
            raise ValueError("Cyclic node group revision is not supported")
        return _memo[key]
    _memo[key] = None
    if _budget is None:
        _budget = [10000, 50000]
    _budget[0] -= len(tree.nodes)
    _budget[1] -= len(tree.links)
    if _depth > 8 or min(_budget) < 0:
        raise ValueError("Tree exceeds the revision inspection bound")
    records = []
    for node in tree.nodes:
        record = _node_info(node)
        record["properties"] = _primitive_data(node)
        for prop in node.bl_rna.properties:
            if prop.type in {"POINTER", "COLLECTION"} and prop.identifier not in {
                "rna_type",
                "id_data",
                "inputs",
                "outputs",
                "internal_links",
                "node_tree",
            }:
                record["properties"][prop.identifier] = _stored_node_data(
                    prop.identifier, getattr(node, prop.identifier), require_complete
                )
        for direction in ("inputs", "outputs"):
            for index, socket in enumerate(getattr(node, direction)):
                value = getattr(socket, "default_value", None)
                if value is not None and getattr(value, "id_data", None) is value:
                    record[direction][index]["value"] = _id_identity(value)
        group = getattr(node, "node_tree", None)
        record["group"] = (
            None
            if group is None
            else {
                "identity": _id_identity(group),
                "revision": _revision(group, require_complete, _depth + 1, _budget, _memo),
            }
        )
        records.append(record)
    interface = getattr(tree, "interface", None)
    interface_records = []
    if interface is not None:
        if len(interface.items_tree) > 10000:
            raise ValueError("Tree interface exceeds the revision inspection bound")
        for item in interface.items_tree:
            record = {
                key: _value(getattr(item, key))
                for key in ("identifier", "name", "item_type", "in_out", "socket_type", "default_value")
                if hasattr(item, key)
            }
            record["parent"] = getattr(getattr(item, "parent", None), "name", None)
            interface_records.append(record)
    state = {
        "interface": interface_records,
        "nodes": records,
        "links": sorted([_link_info(link) for link in tree.links], key=lambda item: item["id"]),
    }
    digest = hashlib.sha256(json.dumps(state, sort_keys=True, default=_value).encode()).hexdigest()
    _memo[key] = digest
    return digest


def _capabilities(tree):
    return {
        "query": True,
        "schema": True,
        "patch": tree.bl_idname in {"ShaderNodeTree", "CompositorNodeTree"},
        "patch_operations": ["set_input", "set_output", "set_label", "set_location", "connect", "disconnect"]
        if tree.bl_idname != "GeometryNodeTree"
        else [],
        "geometry_mutation": "Use existing blender-geometry-nodes v1 tools; no generic transaction",
    }


def _bounded(result):
    if len(json.dumps(result, default=_value).encode()) > 65536:
        raise ValueError("Response exceeds 64 KiB; request a smaller page")
    return result


def query_nodes(tree_ref, offset=0, limit=20, node_names=None, link_offset=0):
    """Read a node page and a bounded page of its incident links."""
    try:
        bpy = _host()
        _, tree = _resolve(bpy, tree_ref)
        _page(offset, limit)
        _page(link_offset, limit)
        if node_names is not None and (
            not isinstance(node_names, list)
            or len(node_names) > 100
            or not all(isinstance(name, str) for name in node_names)
        ):
            raise ValueError("node_names must be at most 100 exact node names")
        nodes = [node for node in tree.nodes if node_names is None or node.name in node_names]
        selected = nodes[offset : offset + limit]
        names = {node.name for node in selected}
        links = [link for link in tree.links if link.from_node.name in names or link.to_node.name in names]
        result = {
            "tree_ref": tree_ref,
            "revision": _revision(tree),
            "capabilities": _capabilities(tree),
            "nodes": [_node_info(node, include_sockets=False) for node in selected],
            "total": len(nodes),
            "next_offset": offset + limit if offset + limit < len(nodes) else None,
            "links": [_link_info(link) for link in links[link_offset : link_offset + limit]],
            "next_link_offset": link_offset + limit if link_offset + limit < len(links) else None,
            "shared_users": int(tree.users),
        }
        return skill_success("Node page", **_bounded(result))
    except Exception as exc:
        return skill_error("Node query failed", "node_query_failed", detail=str(exc))


def _property_schema(owner, prop):
    """RNA schema, adapted from newo's _gn_property_schema (MIT)."""
    record = {
        "identifier": prop.identifier,
        "type": prop.type,
        "readonly": bool(getattr(prop, "is_readonly", False)),
        "array_length": int(getattr(prop, "array_length", 0)),
    }
    for source, destination in (
        ("name", "name"),
        ("description", "description"),
        ("hard_min", "min"),
        ("hard_max", "max"),
    ):
        if hasattr(prop, source):
            record[destination] = _value(getattr(prop, source))
    try:
        if prop.type in {"BOOLEAN", "INT", "FLOAT", "STRING", "ENUM"}:
            record["value"] = _value(getattr(owner, prop.identifier))
    except (AttributeError, TypeError, ValueError, RuntimeError):
        pass
    if prop.type == "ENUM" and not prop.is_readonly:
        try:
            record["enum_items"] = [
                {"identifier": item.identifier, "name": item.name, "description": item.description}
                for item in prop.enum_items
            ]
        except (AttributeError, TypeError, RuntimeError):
            record["enum_unavailable"] = True
    return record


def node_schema(tree_ref, node_name, offset=0, limit=40):
    """Inspect the actual existing node, including its current dynamic sockets.

    No node is instantiated, no defaults are substituted for unknown types.
    RNA COLLECTION/POINTER properties are described but never traversed.
    """
    try:
        bpy = _host()
        _, tree = _resolve(bpy, tree_ref)
        _page(offset, limit)
        node = tree.nodes.get(node_name)
        if node is None:
            raise ValueError("Node not found")
        entries = [("INPUT", index, socket) for index, socket in enumerate(node.inputs)]
        entries += [("OUTPUT", index, socket) for index, socket in enumerate(node.outputs)]
        entries += [
            ("PROPERTY", None, prop)
            for prop in node.bl_rna.properties
            if prop.identifier != "rna_type" and not prop.identifier.startswith("bl_")
        ]
        items = []
        for kind, index, item in entries[offset : offset + limit]:
            if kind == "PROPERTY":
                record = _property_schema(node, item)
            else:
                record = _socket_info(item)
                record.update(
                    index=index,
                    socket_type=getattr(item, "bl_idname", None),
                    unavailable=bool(getattr(item, "is_unavailable", False)),
                )
                prop = next((prop for prop in item.bl_rna.properties if prop.identifier == "default_value"), None)
                record["value_schema"] = _property_schema(item, prop) if prop else None
            record["kind"] = kind
            items.append(record)
        return skill_success(
            "Live node schema",
            **_bounded(
                {
                    "tree_ref": tree_ref,
                    "node": node_name,
                    "node_type": node.bl_idname,
                    "items": items,
                    "total": len(entries),
                    "next_offset": offset + limit if offset + limit < len(entries) else None,
                    "revision": _revision(tree),
                    "probe_created": False,
                }
            ),
        )
    except Exception as exc:
        return skill_error("Node schema failed", "node_schema_failed", detail=str(exc))


def query_targets(offset=0, limit=20):
    """Page owner references without exporting graphs or enabling node trees."""
    try:
        bpy = _host()
        _page(offset, limit)
        targets = []
        for kind, collection_name in _COLLECTIONS.items():
            for owner in getattr(bpy.data, collection_name):
                tree = (
                    owner
                    if kind == "NODE_GROUP"
                    else getattr(owner, "compositing_node_group", getattr(owner, "node_tree", None))
                )
                tree_type = getattr(tree, "bl_idname", None)
                if kind in _TYPES.get(tree_type, set()):
                    targets.append({"tree_type": tree_type, "owner": {"kind": kind, "name": owner.name}})
        return skill_success(
            "Node targets",
            tree_refs=targets[offset : offset + limit],
            total=len(targets),
            next_offset=offset + limit if offset + limit < len(targets) else None,
        )
    except Exception as exc:
        return skill_error("Node targets failed", "node_targets_failed", detail=str(exc))


def _socket(tree, node_name, identifier, direction):
    node = tree.nodes.get(node_name)
    if node is None:
        raise ValueError(f"Node not found: {node_name}")
    if type(node).__module__ != "bpy.types":
        raise ValueError("Python add-on node mutation is not qualified; inspection remains available")
    sockets = node.inputs if direction == "INPUT" else node.outputs
    matches = [socket for socket in sockets if socket.identifier == identifier]
    if len(matches) != 1:
        raise ValueError("Socket identifier must resolve uniquely; query the actual schema")
    socket = matches[0]
    if getattr(socket, "is_unavailable", False):
        raise ValueError("Socket is unavailable in the current node configuration")
    return socket


def _execute(tree, operations, undo):
    for operation in operations:
        op = operation.get("op")
        expected = (
            {"op", "from_node", "from_socket", "to_node", "to_socket"}
            if op in {"connect", "disconnect"}
            else {"op", "node", "socket", "value"}
            if op in {"set_input", "set_output"}
            else {"op", "node", "value"}
        )
        if set(operation) != expected:
            raise ValueError("Patch operation has missing or unknown fields")
        node = tree.nodes.get(operation["node"]) if "node" in operation else None
        if op in {"set_input", "set_output"}:
            socket = _socket(
                tree, operation.get("node"), operation.get("socket"), "INPUT" if op == "set_input" else "OUTPUT"
            )
            value = operation.get("value")
            if not hasattr(socket, "default_value"):
                raise ValueError("Socket has no editable default value")
            prop = next((item for item in socket.bl_rna.properties if item.identifier == "default_value"), None)
            if prop is not None:
                if prop.type not in {"FLOAT", "INT", "BOOLEAN", "STRING", "ENUM"} or prop.is_readonly:
                    raise ValueError("Only primitive socket values are supported")
                _check_value(value, prop)
            previous = _value(socket.default_value)
            undo.append(lambda socket=socket, previous=previous: setattr(socket, "default_value", previous))
            socket.default_value = value
            if not _equal_value(_value(socket.default_value), value):
                raise ValueError("Blender coerced the socket value; refusing silent conversion")
        elif op in {"set_label", "set_location"}:
            if node is None or type(node).__module__ != "bpy.types":
                raise ValueError("Native node not found")
            key = "label" if op == "set_label" else "location"
            value = operation.get("value")
            if key == "label" and not isinstance(value, str):
                raise ValueError("label must be a string")
            if key == "location" and (
                not isinstance(value, list) or len(value) != 2 or not all(type(item) in (int, float) for item in value)
            ):
                raise ValueError("location must contain two numbers")
            previous = _value(getattr(node, key))
            undo.append(lambda node=node, key=key, previous=previous: setattr(node, key, previous))
            setattr(node, key, value)
        elif op in {"connect", "disconnect"}:
            source = _socket(tree, operation.get("from_node"), operation.get("from_socket"), "OUTPUT")
            target = _socket(tree, operation.get("to_node"), operation.get("to_socket"), "INPUT")
            if getattr(target, "is_multi_input", False):
                raise ValueError("Multi-input ordering is not supported by this patch path")
            links = [link for link in tree.links if link.to_socket == target]
            if op == "connect":
                if links:
                    raise ValueError("Target is linked; disconnect explicitly before connecting")
                link = tree.links.new(source, target)
                undo.append(lambda link=link: tree.links.remove(link))
                if not getattr(link, "is_valid", True):
                    raise ValueError("Blender rejected the connection")
            else:
                matched = [link for link in links if link.from_socket == source]
                if len(matched) != 1:
                    raise ValueError("Exact link not found")
                tree.links.remove(matched[0])
                undo.append(lambda source=source, target=target: tree.links.new(source, target))
        else:
            raise ValueError(f"Unsupported patch operation: {op}")


def _equal_value(actual, requested):
    if isinstance(actual, list) and isinstance(requested, list):
        return len(actual) == len(requested) and all(
            _equal_value(left, right) for left, right in zip(actual, requested)
        )
    if type(actual) in (float, int) and type(requested) in (float, int):
        return math.isclose(actual, requested, rel_tol=1e-6, abs_tol=1e-8)
    return actual == requested


def _check_value(value, prop):
    values = value if isinstance(value, list) else [value]
    if prop.type in {"FLOAT", "INT"}:
        for item in values:
            if (
                type(item) not in (int, float)
                or not math.isfinite(item)
                or item < getattr(prop, "hard_min", float("-inf"))
                or item > getattr(prop, "hard_max", float("inf"))
            ):
                raise ValueError("Numeric value lies outside the actual RNA range")
    if prop.type == "INT" and any(type(item) is not int for item in values):
        raise ValueError("Integer sockets require integer values")
    if prop.type == "BOOLEAN" and type(value) is not bool:
        raise ValueError("Boolean sockets require boolean values")
    if prop.type == "STRING" and not isinstance(value, str):
        raise ValueError("String sockets require string values")
    if prop.type == "ENUM" and value not in {item.identifier for item in prop.enum_items}:
        raise ValueError("Enum value is not available in the current node")


def _copy_for_validation(bpy, owner, tree, kind):
    """Copy the owner, or group itself; never edit shared child groups."""
    copy_owner = owner.copy()
    standalone = None
    try:
        copy_owner.name = ".DCC Node Validation"
        if kind == "NODE_GROUP":
            return copy_owner, copy_owner, None
        if kind == "SCENE" and hasattr(owner, "compositing_node_group"):
            standalone = tree.copy()
            copy_owner.compositing_node_group = standalone
            return copy_owner, standalone, standalone
        return copy_owner, copy_owner.node_tree, None
    except Exception:
        getattr(bpy.data, _COLLECTIONS[kind]).remove(copy_owner)
        if standalone is not None:
            bpy.data.node_groups.remove(standalone)
        raise


def patch_nodes(tree_ref, base_revision, operations, action="apply", allow_shared=False):
    """Validate on a disposable copy, then apply reversible local operations.

    A revision is mandatory. Rollback reports measured restoration, never a
    blanket transaction guarantee. Geometry and custom Python nodes are read-only.
    """
    try:
        bpy = _host()
        owner, tree = _resolve(bpy, tree_ref)
        if type(allow_shared) is not bool:
            raise ValueError("allow_shared must be a boolean")
        if action not in {"validate", "apply"}:
            raise ValueError("action must be validate or apply")
        if (
            not isinstance(operations, list)
            or not 1 <= len(operations) <= 100
            or not all(isinstance(op, dict) for op in operations)
        ):
            raise ValueError("operations must contain 1..100 operation objects")
        if not _capabilities(tree)["patch"]:
            raise ValueError("Geometry mutation uses existing v1 tools; generic transaction unavailable")
        if any(
            getattr(item, "library", None) is not None or not getattr(item, "is_editable", True)
            for item in (owner, tree)
        ):
            raise ValueError("Linked or non-editable trees cannot be patched")
        if tree.users > 1 and not allow_shared:
            raise ValueError("Tree has shared users; allow_shared=true explicitly edits all users")
        revision = _revision(tree, require_complete=True)
        if base_revision != revision:
            return skill_error(
                "Stale node revision",
                "stale_revision",
                status="stale_revision",
                current_revision=revision,
                applied=False,
                mutated=False,
            )
        kind = tree_ref["owner"]["kind"]
        dirty_before = bool(bpy.data.is_dirty)
        working_owner = standalone = None
        candidate_revision = None
        validation_error = None
        cleanup_errors = []
        try:
            working_owner, working_tree, standalone = _copy_for_validation(bpy, owner, tree, kind)
            if working_tree is tree:
                raise RuntimeError("Owner copy did not isolate the node tree")
            _execute(working_tree, operations, [])
            candidate_revision = _revision(working_tree, require_complete=True)
        except Exception as exc:
            validation_error = str(exc)
        finally:
            if working_owner is not None:
                try:
                    getattr(bpy.data, _COLLECTIONS[kind]).remove(working_owner)
                except Exception as exc:
                    cleanup_errors.append(str(exc))
            if standalone is not None:
                try:
                    bpy.data.node_groups.remove(standalone)
                except Exception as exc:
                    cleanup_errors.append(str(exc))
        dirty = {"dirty_before": dirty_before, "dirty_after_validation": bool(bpy.data.is_dirty)}
        if cleanup_errors or validation_error:
            return skill_error(
                "Node candidate rejected",
                "candidate_rejected",
                status="rejected",
                applied=False,
                mutated=False,
                validation_error=validation_error,
                cleanup_errors=cleanup_errors,
                **dirty,
            )
        if _revision(tree, require_complete=True) != base_revision:
            return skill_error(
                "Tree changed during validation",
                "stale_revision",
                status="stale_revision",
                applied=False,
                mutated=False,
                **dirty,
            )
        if action == "validate":
            return skill_success(
                "Node candidate validated",
                status="validated",
                applied=False,
                candidate_revision=candidate_revision,
                base_revision=base_revision,
                **dirty,
            )
        undo = []
        try:
            _execute(tree, operations, undo)
            actual = _revision(tree, require_complete=True)
            if actual != candidate_revision:
                raise RuntimeError("Commit readback differs from the validated candidate")
            return skill_success(
                "Node patch applied",
                status="applied",
                applied=True,
                revision=actual,
                operation_count=len(operations),
                shared_users=int(tree.users),
                **dirty,
            )
        except Exception as exc:
            rollback_errors = []
            for restore in reversed(undo):
                try:
                    restore()
                except Exception as rollback_exc:
                    rollback_errors.append(str(rollback_exc))
            try:
                restored = _revision(tree, require_complete=True) == base_revision
            except Exception as rollback_exc:
                restored = False
                rollback_errors.append(str(rollback_exc))
            return skill_error(
                "Node commit failed",
                "node_commit_failed",
                status="rolled_back" if restored else "rollback_incomplete",
                applied=False,
                mutated=not restored,
                rollback_complete=restored,
                rollback_errors=rollback_errors,
                detail=str(exc),
                **dirty,
            )
    except Exception as exc:
        return skill_error(
            "Node patch rejected",
            "node_patch_rejected",
            status="rejected",
            applied=False,
            mutated=False,
            detail=str(exc),
        )
