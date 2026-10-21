"""Check the node seam on a copied material from an already-built NodeBPY scene.

Call ``run_project_node_check()`` on the test-owned Blender main thread after
building the character. No scene/file/asset is opened, reset or removed. The
copied material remains as evidence and is never assigned to an object.
"""

from __future__ import annotations

import json
import math

from dcc_mcp_blender import _node_runtime_ops as runtime
from dcc_mcp_blender._node_graph_ops import _node_info

NPR_TYPES = {"ShaderNodeShaderInfo", "ShaderNodeEeveeLightShaderInfo"}


def _ref(material):
    return {"tree_type": "ShaderNodeTree", "owner": {"kind": "MATERIAL", "name": material.name}}


def _find_npr(material):
    stack = [(material.node_tree, _ref(material), 0)]
    seen = set()
    scanned = 0
    while stack:
        tree, ref, depth = stack.pop()
        pointer = tree.as_pointer()
        if pointer in seen:
            continue
        seen.add(pointer)
        scanned += len(tree.nodes)
        if depth > 8 or scanned > 10000:
            raise AssertionError("Project NPR discovery exceeded the bounded group scan")
        for node in tree.nodes:
            if node.bl_idname in NPR_TYPES:
                return ref, node.name, node.bl_idname
            group = getattr(node, "node_tree", None)
            if group is not None:
                stack.append(
                    (
                        group,
                        {"tree_type": "ShaderNodeTree", "owner": {"kind": "NODE_GROUP", "name": group.name}},
                        depth + 1,
                    )
                )
    return None


def _input_to_edit(tree):
    for node in tree.nodes:
        if type(node).__module__ != "bpy.types":
            continue
        for socket in node.inputs:
            if socket.type != "VALUE" or socket.is_linked or getattr(socket, "is_unavailable", False):
                continue
            prop = socket.bl_rna.properties.get("default_value")
            if prop is None or prop.is_readonly or prop.type != "FLOAT" or prop.array_length:
                continue
            previous = float(socket.default_value)
            for delta in (0.01, -0.01):
                proposed = previous + delta
                if math.isfinite(proposed) and prop.hard_min <= proposed <= prop.hard_max:
                    return node, socket, previous, proposed
    raise AssertionError(
        "Actual material has no supported unlinked native float input; no substitute fixture was created"
    )


def _unrelated(tree, target_name):
    return json.dumps(
        [_node_info(node) for node in tree.nodes if node.name != target_name], sort_keys=True, default=str
    )


def run_project_node_check(material_name=None, patch_material_name=None):
    """Retain a patched copy and return bounded evidence from the actual graph.

    NPR nodes are inspected only when the actual material uses them. To inspect
    one complex material while patching a different assigned layer, pass both
    explicit names; the report distinguishes those two sources.
    """
    import bpy

    assigned = {
        slot.material.name: slot.material
        for obj in bpy.context.scene.objects
        for slot in obj.material_slots
        if slot.material is not None and slot.material.node_tree is not None
    }
    material = assigned.get(material_name) if material_name else next(iter(assigned.values()), None)
    assert material is not None, "Requested assigned node material was not found"
    patch_source = assigned.get(patch_material_name) if patch_material_name else material
    assert patch_source is not None, "Requested assigned patch material was not found"
    original_ref = _ref(material)
    originals = {}
    for source in (material, patch_source):
        observed = runtime.query_nodes(_ref(source), limit=5)
        assert observed["success"], observed
        originals[source.name] = observed
    original = originals[material.name]
    assert original["context"]["nodes"], "Actual material contains no nodes"
    project_schema = runtime.node_schema(original_ref, original["context"]["nodes"][0]["name"], limit=20)
    assert project_schema["success"], project_schema
    npr = _find_npr(material)
    npr_ref = npr_type = None
    npr_schema = None
    if npr is not None:
        npr_ref, npr_name, npr_type = npr
        npr_schema = runtime.node_schema(npr_ref, npr_name, limit=20)
        assert npr_schema["success"], npr_schema
    copied = patch_source.copy()
    copied.name = ".DCC NodeBPY Material Check"
    copied.use_fake_user = True
    assert copied.node_tree != patch_source.node_tree, "Material.copy did not isolate the surface tree"
    copied_ref = _ref(copied)
    node, socket, previous, proposed = _input_to_edit(copied.node_tree)
    unrelated = _unrelated(copied.node_tree, node.name)
    page = runtime.query_nodes(copied_ref, node_names=[node.name], limit=1)
    assert page["success"] and len(page["context"]["nodes"]) == 1, page
    schema = runtime.node_schema(copied_ref, node.name, limit=20)
    assert schema["success"], schema
    operations = [{"op": "set_input", "node": node.name, "socket": socket.identifier, "value": proposed}]
    revision = page["context"]["revision"]
    validation = runtime.patch_nodes(copied_ref, revision, operations, action="validate")
    assert validation["success"], validation
    assert socket.default_value == previous, "Candidate validation changed the real copied material"
    human_label = node.label + " [human intervention]"
    node.label = human_label
    rejected = runtime.patch_nodes(copied_ref, revision, operations)
    assert not rejected["success"] and rejected["context"]["status"] == "stale_revision", rejected
    current = runtime.query_nodes(copied_ref, node_names=[node.name], limit=1)
    applied = runtime.patch_nodes(copied_ref, current["context"]["revision"], operations)
    assert applied["success"], applied
    assert math.isclose(socket.default_value, proposed, rel_tol=1e-6, abs_tol=1e-8)
    assert node.label == human_label, "Commit overwrote the intervening human label"
    assert _unrelated(copied.node_tree, node.name) == unrelated, "Unrelated copied nodes changed"
    for source in (material, patch_source):
        original_after = runtime.query_nodes(_ref(source), limit=1)
        assert original_after["success"], original_after
        assert original_after["context"]["revision"] == originals[source.name]["context"]["revision"], (
            "Original material/shared groups changed"
        )
    return {
        "passed": True,
        "source_material": material.name,
        "patched_source_material": patch_source.name,
        "same_material_query_and_patch": material == patch_source,
        "project_graph_query": original,
        "project_graph_schema_page": project_schema,
        "npr_status": "present" if npr is not None else "absent",
        "retained_material_copy": copied.name,
        "npr_type": npr_type,
        "npr_tree_ref": npr_ref,
        "npr_schema_page": npr_schema,
        "local_query": page,
        "local_schema_page": schema,
        "validation": validation,
        "stale_rejection": rejected,
        "commit": applied,
        "readback": {
            "node": node.name,
            "socket_identifier": socket.identifier,
            "previous": previous,
            "requested": proposed,
            "actual": float(socket.default_value),
        },
        "original_and_shared_groups_unchanged": True,
        "unrelated_nodes_unchanged": True,
        "visual_acceptance": "Not performed",
    }
