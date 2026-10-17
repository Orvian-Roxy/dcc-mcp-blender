"""Run inside an isolated NPR with Wiggle 2 and MMD Tools already enabled.

The coordinator supplies a synthetic PMX. This helper never launches Blender,
installs plugins, opens a main file or saves/changes user preferences.
"""

from __future__ import annotations

from pathlib import Path
from time import perf_counter


def create_synthetic_pmx(path: str, addon_module: str) -> str:
    """Write one triangle and one bone with the installed plugin's PMX writer.

    Pass the exact enabled module, including its full ``bl_ext`` identity for
    Extensions. Do not import the short ``mmd_tools`` name: a Runtime helper
    with that filename can shadow the actual add-on.
    """
    import importlib

    destination = Path(path).expanduser().resolve()
    if destination.exists():
        raise FileExistsError(f"Synthetic input already exists: {destination}")
    if (
        not isinstance(addon_module, str)
        or not addon_module
        or any(not part.isidentifier() or part.startswith("_") for part in addon_module.split("."))
    ):
        raise ValueError("addon_module must be the exact enabled Python module identity.")
    pmx = importlib.import_module(f"{addon_module}.core.pmx")
    model = pmx.Model()
    model.name = model.name_e = "DCC synthetic triangle"
    model.comment = model.comment_e = "Isolated Operator/RNA acceptance input"
    bone = pmx.Bone()
    bone.name = bone.name_e = "Root"
    bone.location = [0.0, 0.0, 0.0]
    bone.displayConnection = [0.0, 1.0, 0.0]
    model.bones.append(bone)
    for coordinate, uv in (
        ([0.0, 0.0, 0.0], [0.0, 0.0]),
        ([1.0, 0.0, 0.0], [1.0, 0.0]),
        ([0.0, 1.0, 0.0], [0.0, 1.0]),
    ):
        vertex = pmx.Vertex()
        vertex.co = coordinate
        vertex.normal = [0.0, 0.0, 1.0]
        vertex.uv = uv
        vertex.weight = pmx.BoneWeight()
        vertex.weight.type = pmx.BoneWeight.BDEF1
        vertex.weight.bones = [0]
        model.vertices.append(vertex)
    model.faces.append((0, 1, 2))
    material = pmx.Material()
    material.name = material.name_e = "Triangle"
    material.diffuse = [0.8, 0.8, 0.8, 1.0]
    material.specular = [0.0, 0.0, 0.0]
    material.ambient = [0.2, 0.2, 0.2]
    material.edge_color = [0.0, 0.0, 0.0, 1.0]
    material.vertex_count = 3
    model.materials.append(material)
    pmx.save(str(destination), model)
    readback = pmx.load(str(destination))
    assert (len(readback.vertices), len(readback.faces), len(readback.bones)) == (3, 1, 1)
    return str(destination)


def run_acceptance(pmx_path: str) -> dict:
    """Exercise the five public RNA tools against real plugin registration."""
    import bpy

    from dcc_mcp_blender import _rna_runtime_ops as rna

    calls = []

    def invoke(name, **kwargs):
        started = perf_counter()
        result = getattr(rna, name)(**kwargs)
        calls.append({"tool": name, "tool_elapsed_seconds": perf_counter() - started, "success": result["success"]})
        assert result["success"], result
        return result["context"]

    pmx = Path(pmx_path).resolve(strict=True)
    owner = {"type": "scene"}
    properties = invoke("get_properties", owner=owner, path="wiggle", query="Quality")["properties"]
    assert any(row["identifier"] == "iterations" and row["value_status"] == "available" for row in properties)
    previous = invoke("get_properties", owner=owner, path="wiggle.iterations")["value"]
    changed = previous + 1 if previous < 32 else previous - 1
    try:
        assert invoke("set_property", owner=owner, path="wiggle.iterations", value=changed)["value"] == changed
        assert invoke("get_properties", owner=owner, path="wiggle.iterations")["value"] == changed
    finally:
        invoke("set_property", owner=owner, path="wiggle.iterations", value=previous)

    search = invoke("search_operators", query="mmd_tools.import_model")
    assert any(row["operator_id"] == "mmd_tools.import_model" for row in search["operators"])
    schema = invoke("describe_operator", operator_id="mmd_tools.import_model")
    assert any(row["identifier"] == "filepath" and row["type"] == "STRING" for row in schema["properties"])
    previous_objects = set(bpy.data.objects.keys())
    outcome = invoke(
        "call_operator",
        operator_id="mmd_tools.import_model",
        parameters={"filepath": str(pmx)},
        context={"mode": "OBJECT"},
    )
    assert outcome["operator_status"] == ["FINISHED"]
    imported = sorted(set(bpy.data.objects.keys()) - previous_objects)
    assert imported, "FINISHED without imported objects does not prove a successful PMX import"
    return {
        "calls": calls,
        "tool_calls": len(calls),
        "ad_hoc_python": 0,
        "control_seconds_excluding_operator": sum(
            call["tool_elapsed_seconds"] for call in calls if call["tool"] != "call_operator"
        ),
        "operator_elapsed_seconds": sum(
            call["tool_elapsed_seconds"] for call in calls if call["tool"] == "call_operator"
        ),
        "imported_objects": imported,
        "operator_schema": schema,
        "operator_result": outcome,
    }
