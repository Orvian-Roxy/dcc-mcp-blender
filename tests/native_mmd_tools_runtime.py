"""Synthetic native MMD checks for the coordinator's isolated alpha.4 instance.

Run prepare_input, then import/build through actual external DCC CLI calls.
The snapshot and settings helpers return in-process evidence, never RPC counts.
No launch, shutdown, addon registration, production configuration or scene cleanup.
"""

from __future__ import annotations

import importlib
import runpy
import sys
from pathlib import Path

_MODEL_NAME = "DCC native MMD acceptance"


def prepare_input(work_dir: str, addon_module: str) -> dict:
    """Create a private PMX with one triangle, one bone and two sphere bodies."""
    directory = Path(work_dir).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "native-mmd-synthetic.pmx"
    helper = runpy.run_path(str(Path(__file__).with_name("native_rna_runtime.py")))
    helper["create_synthetic_pmx"](str(path), addon_module)
    pmx = importlib.import_module(f"{addon_module}.core.pmx")
    model = pmx.load(str(path))
    model.name = model.name_e = _MODEL_NAME
    for index, mask in enumerate((0xFFFE, 0xFFFF)):
        rigid = pmx.Rigid()
        rigid.name = rigid.name_e = f"DCC sphere {index}"
        rigid.bone = 0
        rigid.collision_group_number = 0
        # PMX bits mean allow-collision; imported MMD RNA mask booleans mean ignore.
        rigid.collision_group_mask = mask
        rigid.type = pmx.Rigid.TYPE_SPHERE
        rigid.size = [0.25, 0.25, 0.25]
        rigid.location = [float(index), 0.0, 1.0]
        rigid.rotation = [0.0, 0.0, 0.0]
        rigid.mass = 1.0
        rigid.velocity_attenuation = 0.1
        rigid.rotation_attenuation = 0.1
        rigid.bounce = 0.0
        rigid.friction = 0.5
        rigid.mode = pmx.Rigid.MODE_STATIC if index == 0 else pmx.Rigid.MODE_DYNAMIC
        model.rigids.append(rigid)
    pmx.save(str(path), model)
    readback = pmx.load(str(path))
    assert (len(readback.vertices), len(readback.faces), len(readback.bones), len(readback.rigids)) == (3, 1, 1, 2)
    assert [body.collision_group_mask for body in readback.rigids] == [0xFFFE, 0xFFFF]
    return {
        "stage": "prepare_input",
        "execution": "in_process_fixture_writer",
        "filepath": str(path),
        "addon_module": addon_module,
        "synthetic_model_name": _MODEL_NAME,
        "import_call": {
            "operator_id": "mmd_tools.import_model",
            "parameters": {"filepath": str(path)},
            "context": {"mode": "OBJECT"},
        },
        "build_operator": "mmd_tools.build_rig",
        "expected": {"rigid_bodies": 2, "native_relations": 1, "collision_helpers": 0},
    }


def _root(root_name: str | None):
    import bpy

    candidates = [
        obj
        for obj in bpy.context.scene.objects
        if obj.mmd_type == "ROOT" and obj.mmd_root.name == _MODEL_NAME and (root_name is None or obj.name == root_name)
    ]
    if len(candidates) != 1:
        raise ValueError("Expected exactly one imported synthetic MMD root in the current scene.")
    return candidates[0]


def snapshot(addon_module: str, root_name: str | None = None, require_built: bool = True) -> dict:
    """Inspect only this synthetic model and assert the requested build stage."""
    import bpy

    model_api = importlib.import_module(f"{addon_module}.core.model")
    root = _root(root_name)
    bodies = list(model_api.Model(root).rigidBodies())
    assert len(bodies) == 2
    assert all(body.rigid_body is not None for body in bodies)
    assert all(hasattr(body.rigid_body, "collision_exclusions") for body in bodies)
    relations = sorted(
        (body.name, entry.rigid_body.name)
        for body in bodies
        for entry in body.rigid_body.collision_exclusions
        if entry.rigid_body is not None
    )
    helpers = sorted(obj.name for obj in root.children_recursive if obj.mmd_type == "NON_COLLISION_CONSTRAINT")
    mmr_enabled = sorted(
        name
        for name, module in list(sys.modules.items())
        if any(part.lower().startswith("mmr") for part in name.split("."))
        and getattr(module, "__addon_enabled__", False)
    )
    mmr_namespaces = sorted(name for name in dir(bpy.ops) if "mmr" in name.lower())
    assert not mmr_enabled and not mmr_namespaces, "This qualification requires MMD Tools without MMR registration."
    built = bool(root.mmd_root.is_built)
    backend = root.get("mmd_tools_collision_backend")
    if require_built:
        assert built and backend == "native_pairs_v1"
        assert len(relations) == 1 and not helpers
    else:
        assert not built and not helpers
    return {
        "stage": "built_snapshot" if require_built else "imported_snapshot",
        "execution": "in_process_read_only",
        "root_name": root.name,
        "rigid_bodies": [body.name for body in bodies],
        "relations": relations,
        "backend": backend,
        "is_built": built,
        "collision_helpers": helpers,
        "enabled_mmr_modules": mmr_enabled,
        "registered_mmr_namespaces": mmr_namespaces,
        "build_call": {
            "operator_id": "mmd_tools.build_rig",
            "parameters": {},
            "context": {"active_object": root.name, "selected_objects": [root.name], "mode": "OBJECT"},
        },
    }


def exercise_settings(root_name: str | None = None) -> dict:
    """Read/write/restore world and body mask through public generic RNA APIs."""
    from dcc_mcp_blender import _rna_runtime_ops as rna

    root = _root(root_name)
    body = next(obj for obj in root.children_recursive if obj.mmd_type == "RIGID_BODY")
    calls = []

    def invoke(tool, **kwargs):
        result = getattr(rna, tool)(**kwargs)
        calls.append({"tool": tool, "arguments": kwargs, "result": result})
        assert result["success"], result
        return result["context"]["value"]

    targets = [
        ({"type": "scene"}, "rigidbody_world.time_scale", lambda previous: 0.75 if previous != 0.75 else 1.0),
        (
            {"type": "object", "name": body.name},
            "mmd_rigid.collision_group_mask",
            lambda previous: [not previous[0], *previous[1:]],
        ),
    ]
    restored = []
    for owner, path, change in targets:
        previous = invoke("get_properties", owner=owner, path=path)
        changed = change(previous)
        try:
            assert invoke("set_property", owner=owner, path=path, value=changed) == changed
            assert invoke("get_properties", owner=owner, path=path) == changed
        finally:
            actual = invoke("set_property", owner=owner, path=path, value=previous)
            assert actual == previous
            restored.append({"owner": owner, "path": path, "value": actual})
    return {
        "stage": "settings_roundtrip",
        "execution": "in_process_public_generic_rna",
        "calls": calls,
        "restored": restored,
        "rpc_calls": None,
    }
