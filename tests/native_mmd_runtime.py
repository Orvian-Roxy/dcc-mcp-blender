"""Synthetic native #17 checks for a coordinator-owned, already prepared NPR.

Call run(bpy, runtime_root) on its main thread. This module never launches Blender,
installs plugins, deletes data, saves preferences, or resets an existing scene.
It leaves its synthetic objects and reports plugin absence per case.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from dcc_mcp_blender._native_runtime_ops import call


def run(bpy, runtime_root: str) -> dict:
    records = []

    def record(name, result):
        records.append({"name": name, "result": result})

    def invoke(method, request=None):
        result = call(method, {} if request is None else {"request": request})
        record(method, result)
        return result

    def ok(method, request):
        result = invoke(method, request)
        assert result.get("status") == "ok", result
        return result

    outcomes = {}
    for family in ("toon", "mbts", "wiggle"):
        capabilities = invoke(f"mmd.{family}.capabilities")
        data = capabilities.get("data", {})
        ready = data.get("mutating_look_operations_available") if family == "toon" else data.get("ready")
        if capabilities.get("status") != "ok" or not ready:
            outcomes[family] = {"state": "unavailable", "capabilities": capabilities}
            continue
        try:
            if family == "toon":
                path = Path(runtime_root) / "tests/client/native_toon_transaction.py"
                spec = importlib.util.spec_from_file_location("native_toon_fixture", path)
                fixture = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(fixture)
                toon_result = fixture.run(
                    bpy,
                    lambda action, **kwargs: call(
                        f"mmd.toon.{action}", {} if action == "capabilities" else {"request": kwargs}
                    ),
                    record,
                )
                outcomes[family] = {"state": "passed", "result": toon_result}
            elif family == "mbts":
                name = "DccNativeMbtsFixture"
                assert bpy.data.objects.get(name) is None, "Preserve prior fixture before retry"
                mesh = bpy.data.meshes.new(name)
                mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 0, 1)], [], [(0, 1, 2)])
                obj = bpy.data.objects.new(name, mesh)
                bpy.context.scene.collection.objects.link(obj)
                request = {"operation": "add_geom_stroke", "object_names": [name]}
                ok("mmd.mbts.plan", request)
                ok("mmd.mbts.execute", request)
                assert any(mod.node_group for mod in obj.modifiers if mod.type == "NODES")
                ok("mmd.mbts.execute", {"operation": "remove_geom_stroke", "object_names": [name]})
                assert not any(mod.name.startswith("MBTs_edge") for mod in obj.modifiers)
                outcomes[family] = {"state": "passed", "fixture": name}
            else:
                outcomes[family] = _wiggle(bpy, ok, invoke)
        except Exception as exc:
            outcomes[family] = {"state": "failed", "error": f"{type(exc).__name__}: {exc}"}
    return {"cases": outcomes, "records": records}


def _wiggle(bpy, ok, invoke):
    root_name, rig_name = "DccNativeMmdRoot", "DccNativeWiggleRig"
    assert all(bpy.data.objects.get(name) is None for name in (root_name, rig_name))
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    root = bpy.data.objects.new(root_name, None)
    bpy.context.scene.collection.objects.link(root)
    root.mmd_type = "ROOT"
    rig = bpy.data.objects.new(rig_name, bpy.data.armatures.new(rig_name))
    bpy.context.scene.collection.objects.link(rig)
    rig.parent = root
    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    rig.select_set(True)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    for index, name in enumerate(("Hair", "HairOther")):
        bone = rig.data.edit_bones.new(name)
        bone.head = (index, 0, 0)
        bone.tail = (index, 0, 1)
    bpy.ops.object.mode_set(mode="POSE")
    return _check_wiggle(bpy, root, rig, ok, invoke)


def _selection_owner(pose_bone):
    return pose_bone if hasattr(pose_bone, "select") else pose_bone.bone


def _check_wiggle(bpy, root, rig, ok, invoke):
    for bone in rig.pose.bones:
        _selection_owner(bone).select = True
    rig.data.bones.active = rig.data.bones["Hair"]
    target, other = rig.pose.bones["Hair"], rig.pose.bones["HairOther"]
    original = target.wiggle_stiff
    other_original = other.wiggle_stiff
    request = {
        "model_ref": root.name,
        "armature_name": rig.name,
        "bone_names": ["Hair"],
        "endpoint": "tail",
        "parameters": {"stiffness": 300},
    }
    ok("mmd.wiggle.plan", request)
    applied = ok("mmd.wiggle.apply", request)
    receipt = {"transaction_id": applied["data"]["transaction_id"]}
    assert target.wiggle_stiff == 300 and other.wiggle_stiff == other_original
    assert {bone.name for bone in rig.pose.bones if _selection_owner(bone).select} == {"Hair", "HairOther"}
    ok("mmd.wiggle.validate", receipt)
    # The following user edit targets one bone; avoid the plugin selection callback.
    _selection_owner(other).select = False
    target.wiggle_stiff = 301
    drift = invoke("mmd.wiggle.restore", receipt)
    assert drift.get("status") == "error" and target.wiggle_stiff == 301, drift
    target.wiggle_stiff = 300
    ok("mmd.wiggle.restore", receipt)
    assert target.wiggle_stiff == original and other.wiggle_stiff == other_original
    return {"state": "passed", "fixture": rig.name, "drift_code": drift.get("code")}


def resume_wiggle(bpy):
    """Continue only the known synthetic pair left before the first plan/apply."""
    root = bpy.data.objects.get("DccNativeMmdRoot")
    rig = bpy.data.objects.get("DccNativeWiggleRig")
    assert root is not None and root.mmd_type == "ROOT"
    assert rig is not None and rig.type == "ARMATURE" and rig.parent == root
    assert {bone.name for bone in rig.pose.bones} == {"Hair", "HairOther"}
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    rig.select_set(True)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="POSE")
    records = []

    def invoke(method, request):
        result = call(method, {"request": request})
        records.append({"name": method, "result": result})
        return result

    def ok(method, request):
        result = invoke(method, request)
        assert result.get("status") == "ok", result
        return result

    result = _check_wiggle(bpy, root, rig, ok, invoke)
    return {"case": result, "records": records}
