"""Native node seam checks; create/remove only their own disposable data.

Run ``run_native_checks(npr_node_type=...)`` from an isolated Blender main thread.
The coordinator owns launching Blender and collecting version/dirty evidence.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from dcc_mcp_blender import _node_runtime_ops as runtime
from dcc_mcp_blender._geometry_interface_ops import inspect_geometry_node_interface
from dcc_mcp_blender._node_graph_ops import list_node_links, list_nodes

pytestmark = pytest.mark.e2e


def run_native_checks(npr_node_type=None):
    import bpy

    created = []
    dirty_before = bool(bpy.data.is_dirty)
    results = {}
    try:
        material = bpy.data.materials.new(".DCC Node Runtime Check")
        created.append((bpy.data.materials, material))
        material.use_nodes = True
        tree = material.node_tree
        driver = tree.nodes.new("ShaderNodeValue")
        shader = next(node for node in tree.nodes if node.type == "BSDF_PRINCIPLED")
        ref = {"tree_type": "ShaderNodeTree", "owner": {"kind": "MATERIAL", "name": material.name}}
        page = runtime.query_nodes(ref, node_names=[driver.name, shader.name])
        assert page["success"], page
        legacy_ref = {"kind": "shader", "material_name": material.name}
        baseline_nodes = list_nodes(legacy_ref)
        baseline_links = list_node_links(legacy_ref)
        assert baseline_nodes["success"] and baseline_links["success"]
        selected_names = {driver.name, shader.name}
        projected = [
            {key: value for key, value in node.items() if key not in {"inputs", "outputs"}}
            for node in baseline_nodes["context"]["nodes"]
            if node["name"] in selected_names
        ]
        assert projected == page["context"]["nodes"]
        assert baseline_links["context"]["links"] == page["context"]["links"]
        results["bounded_query_comparison"] = {
            "existing_typed_calls": 2,
            "runtime_typed_calls": 1,
            "existing_return_bytes": len(json.dumps([baseline_nodes, baseline_links]).encode()),
            "runtime_return_bytes": len(json.dumps(page).encode()),
            "equivalent_projection_verified": True,
            "temporary_python_calls": 0,
            "note": "Typed-call count; execute_python or batch can combine baseline RPCs. No latency claim.",
        }
        revision = page["context"]["revision"]
        patch = [
            {
                "op": "connect",
                "from_node": driver.name,
                "from_socket": driver.outputs[0].identifier,
                "to_node": shader.name,
                "to_socket": shader.inputs["Roughness"].identifier,
            }
        ]
        validated = runtime.patch_nodes(ref, revision, patch, action="validate")
        assert validated["success"], validated
        shader.label = "intervening human edit"
        stale = runtime.patch_nodes(ref, revision, patch)
        assert stale["context"]["status"] == "stale_revision", stale
        revision = runtime.query_nodes(ref)["context"]["revision"]
        applied = runtime.patch_nodes(ref, revision, patch)
        assert applied["success"], applied
        assert any(link.from_node == driver and link.to_socket == shader.inputs["Roughness"] for link in tree.links)
        assert shader.label == "intervening human edit"
        results["shader"] = applied

        mix = tree.nodes.new("ShaderNodeMix")
        mix.data_type = "RGBA"
        schema = runtime.node_schema(ref, mix.name, limit=100)
        assert schema["success"], schema
        assert any(row.get("identifier") == "data_type" and row.get("enum_items") for row in schema["context"]["items"])
        results["dynamic_schema"] = schema

        group = bpy.data.node_groups.new(".DCC Shared Check", "ShaderNodeTree")
        created.append((bpy.data.node_groups, group))
        group.interface.new_socket(name="Shadow", in_out="INPUT", socket_type="NodeSocketFloat")
        for _ in range(2):
            node = tree.nodes.new("ShaderNodeGroup")
            node.node_tree = group
        assert group.users == 2
        group_node = group.nodes.new("ShaderNodeValue")
        shared_ref = {"tree_type": "ShaderNodeTree", "owner": {"kind": "NODE_GROUP", "name": group.name}}
        revision = runtime.query_nodes(shared_ref)["context"]["revision"]
        denied = runtime.patch_nodes(
            shared_ref, revision, [{"op": "set_label", "node": group_node.name, "value": "shared"}]
        )
        assert not denied["success"], denied
        results["shared_group"] = denied

        compositor = bpy.data.node_groups.new(".DCC Compositor Check", "CompositorNodeTree")
        created.append((bpy.data.node_groups, compositor))
        comp_node = compositor.nodes.new("CompositorNodeRGB")
        comp_ref = {"tree_type": "CompositorNodeTree", "owner": {"kind": "NODE_GROUP", "name": compositor.name}}
        revision = runtime.query_nodes(comp_ref)["context"]["revision"]
        result = runtime.patch_nodes(
            comp_ref,
            revision,
            [
                {
                    "op": "set_output",
                    "node": comp_node.name,
                    "socket": comp_node.outputs[0].identifier,
                    "value": [0.2, 0.3, 0.4, 1.0],
                }
            ],
        )
        assert result["success"], result
        results["compositor"] = result

        geometry = bpy.data.node_groups.new(".DCC Geometry Check", "GeometryNodeTree")
        created.append((bpy.data.node_groups, geometry))
        geo_ref = {"tree_type": "GeometryNodeTree", "owner": {"kind": "NODE_GROUP", "name": geometry.name}}
        geo = runtime.query_nodes(geo_ref)
        assert geo["success"] and not geo["context"]["capabilities"]["patch"]
        # Existing v1 mutation path remains available, without a new transaction.
        path = (
            Path(__file__).parents[2]
            / "src/dcc_mcp_blender/skills/blender-geometry-nodes/scripts/create_geometry_node_socket.py"
        )
        spec = importlib.util.spec_from_file_location("node_runtime_geometry_v1", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        v1 = module.main(
            group_name=geometry.name,
            name="Weight",
            socket_type="NodeSocketFloat",
            in_out="INPUT",
            expected_revision=inspect_geometry_node_interface(geometry.name)["context"]["revision"],
        )
        assert v1["success"], v1
        results["geometry_v1"] = v1

        if npr_node_type:
            node = tree.nodes.new(npr_node_type)
            results["npr_schema"] = runtime.node_schema(ref, node.name, limit=100)
            assert results["npr_schema"]["success"], results["npr_schema"]
        else:
            results["npr_schema"] = {"qualified": False, "reason": "NPR node type was not provided"}
    finally:
        # Remove owners before their referenced groups. Never remove pre-existing data.
        for collection, item in created:
            collection.remove(item)
    results["dirty_before"] = dirty_before
    results["dirty_after"] = bool(bpy.data.is_dirty)
    return results


def test_native_node_runtime():
    pytest.importorskip("bpy")
    results = run_native_checks()
    assert results["shader"]["success"]


def run_native_revision_checks():
    """Native ID replacement, ramp, curve and shared-group stale-edit checks."""
    import bpy

    created = []
    results = {}
    try:
        material = bpy.data.materials.new(".DCC Revision Check")
        created.append((bpy.data.materials, material))
        material.use_nodes = True
        tree = material.node_tree
        ref = {"tree_type": "ShaderNodeTree", "owner": {"kind": "MATERIAL", "name": material.name}}
        destination = tree.nodes.new("ShaderNodeValue")
        operations = [{"op": "set_label", "node": destination.name, "value": "should not commit"}]

        def rejects_previous_revision(name, edit):
            before = runtime.query_nodes(ref)
            assert before["success"], before
            edit()
            result = runtime.patch_nodes(ref, before["context"]["revision"], operations)
            assert not result["success"] and result["context"]["status"] == "stale_revision", result
            assert destination.label == ""
            results[name] = result

        image_node = tree.nodes.new("ShaderNodeTexImage")
        first = bpy.data.images.new(".DCC Revision Image", width=1, height=1)
        created.append((bpy.data.images, first))
        second = bpy.data.images.new(".DCC Revision Image Replacement", width=1, height=1)
        created.append((bpy.data.images, second))
        image_node.image = first
        old_name = first.name

        def replace_same_name_image():
            first.name = old_name + ".previous"
            second.name = old_name
            image_node.image = second

        rejects_previous_revision("same_name_image_id", replace_same_name_image)

        ramp = tree.nodes.new("ShaderNodeValToRGB")
        rejects_previous_revision(
            "color_ramp", lambda: setattr(ramp.color_ramp.elements[0], "color", (0.2, 0.3, 0.4, 1.0))
        )
        curve = tree.nodes.new("ShaderNodeRGBCurve")

        def edit_curve():
            curve.mapping.curves[0].points[0].location = (0.1, 0.2)
            curve.mapping.update()

        rejects_previous_revision("curve_mapping", edit_curve)

        group = bpy.data.node_groups.new(".DCC Revision Shared", "ShaderNodeTree")
        created.append((bpy.data.node_groups, group))
        group_node = group.nodes.new("ShaderNodeValue")
        for _ in range(2):
            instance = tree.nodes.new("ShaderNodeGroup")
            instance.node_tree = group
        rejects_previous_revision("shared_group_edit", lambda: setattr(group_node, "label", "human edit"))
        # Complete supported storage must still permit a fresh validated patch.
        revision = runtime.query_nodes(ref)["context"]["revision"]
        applied = runtime.patch_nodes(ref, revision, operations)
        assert applied["success"], applied
        results["fresh_revision_applies"] = applied
    finally:
        for collection, item in created:
            collection.remove(item)
    return results


def test_native_revision_checks():
    pytest.importorskip("bpy")
    assert run_native_revision_checks()["fresh_revision_applies"]["success"]
