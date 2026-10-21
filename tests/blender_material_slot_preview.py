"""Native material-slot preview regression on the explicitly owned Linnai copy.

Call through blender-dev/run_script with args={"action": "prepare"}. Submit the
returned targets/variants to the public preview_variants tool, then call this
script with args={"action": "check_cleanup", "preview_context": <tool context>}.
The optional context checks the actual public receipt; this driver never calls
preview, captures images, changes the view, saves, opens or launches Blender.
"""


def _run(args):
    import hashlib
    import json
    from pathlib import Path
    from uuid import uuid4

    import bpy

    from dcc_mcp_blender import _node_runtime_ops as nodes

    expected = Path(
        "/Users/chenrui/Desktop/课件/REPO/项目/NodeBPY-Assets/WutheringWaves/characters/"
        "linnai/work/diagnosis-system-20261020/native-slot-test.blend"
    )
    marker = "_dcc_native_material_slot_preview_v1"
    if not bpy.data.filepath or Path(bpy.data.filepath).resolve() != expected.resolve():
        raise RuntimeError("Refusing any file other than the owned native-slot-test.blend")
    if not isinstance(args, dict) or args.get("action") not in {"prepare", "check_cleanup"}:
        raise ValueError("ARGS.action must be prepare or check_cleanup")

    def name(value):
        return value.name if value is not None else None

    def material(value):
        result = bpy.data.materials.get(value) if value is not None else None
        if value is not None and result is None:
            raise RuntimeError("Required original material disappeared: " + value)
        return result

    def digest(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()

    def slots(obj):
        return [{"link": slot.link, "material": name(slot.material)} for slot in obj.material_slots]

    def underlying(obj, indices):
        records = []
        for index in indices:
            slot = obj.material_slots[index]
            link = slot.link
            try:
                slot.link = "OBJECT"
                records.append({"index": index, "link": link, "override": name(slot.material)})
            finally:
                slot.link = link
        return records

    def mesh_state(obj):
        mesh = obj.data
        return {
            "pointer": mesh.as_pointer(),
            "materials": [name(item) for item in mesh.materials],
            "vertices": len(mesh.vertices),
            "polygons": len(mesh.polygons),
            "loops": len(mesh.loops),
        }

    def material_state(names):
        records, trees, seen, memo = [], [], set(), {}
        for item in names:
            mat = material(item)
            records.append({"name": item, "properties": nodes._primitive_data(mat)})
            if mat.node_tree is not None:
                trees.append(mat.node_tree)
        graphs = []
        while trees:
            tree = trees.pop()
            pointer = tree.as_pointer()
            if pointer in seen:
                continue
            seen.add(pointer)
            graphs.append({"pointer": pointer, "revision": nodes._revision(tree, require_complete=True, _memo=memo)})
            for node in tree.nodes:
                for attr in ("node_tree", "nprtree"):
                    child = getattr(node, attr, None)
                    if child is not None:
                        trees.append(child)
        return digest({"materials": records, "graphs": sorted(graphs, key=lambda row: row["pointer"])})

    def remember(state):
        bpy.context.scene[marker] = json.dumps(state, sort_keys=True, allow_nan=False)

    def cleanup(state, obj):
        for row in state["original_underlying"]:
            slot = obj.material_slots[row["index"]]
            slot.link = "OBJECT"
            slot.material = material(row["override"])
            slot.link = row["link"]
        if slots(obj) != state["original_slots"] or mesh_state(obj) != state["mesh_state"]:
            raise RuntimeError("Preparation cleanup did not restore original slot/mesh state")
        if underlying(obj, (19, 20)) != state["original_underlying"]:
            raise RuntimeError("Preparation cleanup did not restore dormant object materials")
        sibling = bpy.data.objects.get(state.get("sibling", ""))
        if sibling is not None:
            if sibling.get(marker) != state["token"] or sibling.data != obj.data:
                raise RuntimeError("Owned sibling identity changed; retaining evidence")
            bpy.data.objects.remove(sibling, do_unlink=True)
        for item in state["created_materials"]:
            mat = bpy.data.materials.get(item)
            if mat is not None:
                if mat.get(marker) != state["token"] or mat.users:
                    raise RuntimeError("Diagnostic material is no longer unused and owned: " + item)
                bpy.data.materials.remove(mat)
        if material_state(state["original_materials"]) != state["original_material_state"]:
            raise RuntimeError("Original material/node state differs after cleanup")
        del bpy.context.scene[marker]

    if args["action"] == "prepare":
        if marker in bpy.context.scene:
            raise RuntimeError("This scene already has a native slot test; check_cleanup it first")
        obj = bpy.context.scene.objects.get("Linnai_mesh")
        if obj is None:
            candidates = [
                item for item in bpy.context.scene.objects if item.type == "MESH" and len(item.material_slots) == 35
            ]
            candidates.sort(key=lambda item: len(item.data.polygons), reverse=True)
            if not candidates or (
                len(candidates) > 1 and len(candidates[0].data.polygons) == len(candidates[1].data.polygons)
            ):
                raise RuntimeError("Actual 35-slot character mesh is missing or ambiguous")
            obj = candidates[0]
        if (
            obj.type != "MESH"
            or obj.mode != "OBJECT"
            or len(obj.material_slots) != 35
            or any(item.library is not None or not item.is_editable for item in (obj, obj.data))
        ):
            raise RuntimeError("Expected the local editable 35-slot Linnai mesh in Object mode")
        dirty_before = bool(bpy.data.is_dirty)
        original_underlying = underlying(obj, (19, 20))
        originals = sorted(
            {name(mat) for mat in obj.data.materials if mat is not None}
            | {name(slot.material) for slot in obj.material_slots if slot.material is not None}
            | {row["override"] for row in original_underlying if row["override"] is not None}
        )
        state = {
            "schema": 1,
            "token": uuid4().hex,
            "mesh_object": obj.name,
            "mesh_state": mesh_state(obj),
            "original_slots": slots(obj),
            "original_underlying": original_underlying,
            "original_materials": originals,
            "original_material_state": material_state(originals),
            "created_materials": [],
            "dirty_before_prepare": dirty_before,
        }
        remember(state)
        try:
            diagnostic = []
            for kind in ("Transparent", "Emission"):
                mat = bpy.data.materials.new(".DCC Slot Test " + state["token"] + " " + kind)
                mat[marker] = state["token"]
                state["created_materials"].append(mat.name)
                remember(state)
                mat.use_nodes = True
                mat.node_tree.nodes.clear()
                output = mat.node_tree.nodes.new("ShaderNodeOutputMaterial")
                shader = mat.node_tree.nodes.new(
                    "ShaderNodeBsdfTransparent" if kind == "Transparent" else "ShaderNodeEmission"
                )
                shader.inputs["Color"].default_value = (1, 1, 1, 1) if kind == "Transparent" else (1, 0.015, 0.65, 1)
                if kind == "Transparent":
                    mat.surface_render_method = "BLENDED"
                else:
                    shader.inputs["Strength"].default_value = 1.0
                mat.node_tree.links.new(shader.outputs[0], output.inputs["Surface"])
                diagnostic.append(mat)
            transparent, emission = diagnostic
            sibling = bpy.data.objects.new(".DCC Slot Test " + state["token"] + " Shared Mesh", obj.data)
            sibling[marker] = state["token"]
            state["sibling"] = sibling.name
            remember(state)
            bpy.context.scene.collection.objects.link(sibling)
            sibling.hide_render = True
            sibling.hide_viewport = True
            if len(sibling.material_slots) != len(obj.material_slots):
                raise RuntimeError("Shared mesh sibling did not retain the actual material slots")
            slot19, slot20 = obj.material_slots[19], obj.material_slots[20]
            material20 = slot20.material
            slot19.link = "OBJECT"
            slot19.material = emission
            slot19.link = "DATA"
            slot20.link = "OBJECT"
            slot20.material = material20
            state.update(
                prepared_slots=slots(obj),
                prepared_underlying=underlying(obj, (19, 20)),
                sibling_slots=slots(sibling),
                dirty_before_preview=bool(bpy.data.is_dirty),
                prepared_material_state=material_state(originals + state["created_materials"]),
            )
            remember(state)
        except Exception:
            cleanup(state, obj)
            raise
        targets = [
            {
                "id": "slot" + str(index),
                "owner": {"type": "object", "name": obj.name},
                "material_slot": index,
                "base_link": obj.material_slots[index].link,
                "base_material": name(obj.material_slots[index].material),
            }
            for index in (19, 20)
        ]

        def patch(index, mat):
            return {"target": "slot" + str(index), "operations": [{"op": "set_material", "material": mat.name}]}

        return {
            "passed": True,
            "action": "prepare",
            "mesh_object": obj.name,
            "targets": targets,
            "variants": [
                {"id": "baseline", "patches": []},
                {"id": "slot19-transparent", "patches": [patch(19, transparent)]},
                {"id": "slot20-emission", "patches": [patch(20, emission)]},
                {"id": "both", "patches": [patch(19, transparent), patch(20, emission)]},
            ],
            "dirty_before_prepare": dirty_before,
            "dirty_before_preview": state["dirty_before_preview"],
            "checks": [
                "same fixed view for all candidates",
                "shared mesh and other slots unchanged",
                "DATA dormant override and OBJECT binding restored",
                "material/node states unchanged",
                "existing dirty preserved; no blend save",
            ],
        }

    if marker not in bpy.context.scene:
        raise RuntimeError("No prepared native slot test exists in this scene")
    state = json.loads(bpy.context.scene[marker])
    if state.get("schema") != 1:
        raise RuntimeError("Unknown native slot test marker")
    obj = bpy.data.objects.get(state["mesh_object"])
    sibling = bpy.data.objects.get(state["sibling"])
    if obj is None or sibling is None:
        raise RuntimeError("Prepared character or owned sibling disappeared; retaining evidence")
    checks = {
        "target_and_other_slots": slots(obj) == state["prepared_slots"],
        "data_and_dormant_object_bindings": underlying(obj, (19, 20)) == state["prepared_underlying"],
        "mesh_materials_and_counts": mesh_state(obj) == state["mesh_state"],
        "shared_mesh_object": sibling.data == obj.data
        and slots(sibling) == state["sibling_slots"]
        and sibling.hide_render
        and sibling.hide_viewport
        and sibling.get(marker) == state["token"],
        "materials_and_nodes": material_state(state["original_materials"] + state["created_materials"])
        == state["prepared_material_state"],
        "existing_dirty_preserved": not state["dirty_before_preview"] or bool(bpy.data.is_dirty),
    }
    context = args.get("preview_context")
    dirty_exercised = state["dirty_before_preview"]
    if context is not None:
        checks["public_preview_restored"] = context.get("restored") is True and context.get("mutated") is False
        checks["public_preview_completed"] = [item.get("id") for item in context.get("results", [])] == [
            "baseline",
            "slot19-transparent",
            "slot20-emission",
            "both",
        ] and all(
            item.get("status") == "complete" and item.get("restored") is True and item.get("images")
            for item in context.get("results", [])
        )
        dirty_exercised = bool(context.get("dirty_before"))
        checks["public_preview_dirty_preserved"] = not dirty_exercised or (
            context.get("dirty_after") is True and bool(bpy.data.is_dirty)
        )
    if not all(checks.values()):
        return {
            "passed": False,
            "action": "check_cleanup",
            "checks": checks,
            "cleaned": False,
            "detail": "State differs; marker and diagnostics retained for investigation",
        }
    cleanup(state, obj)
    return {
        "passed": True,
        "action": "check_cleanup",
        "checks": checks,
        "cleaned": True,
        "preview_receipt_checked": context is not None,
        "dirty_case_exercised": dirty_exercised,
        "dirty_after_cleanup": bool(bpy.data.is_dirty),
        "removed_objects": 1,
        "removed_materials": len(state["created_materials"]),
    }


if __name__ == "__main__":
    result = _run(globals().get("ARGS"))
    del _run
