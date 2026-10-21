"""Parameter previews render independent candidates and restore the live baseline."""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace as NS

import pytest
from test_node_runtime_ops import Collection, Node

from dcc_mcp_blender import _managed_writes
from dcc_mcp_blender import _node_preview_ops as preview
from dcc_mcp_blender import _node_runtime_ops as nodes


class MaterialSlot:
    """Model the dormant object override behind a DATA-linked Blender slot."""

    def __init__(self, mesh, index, link, override):
        self.mesh, self.index, self.link, self.override = mesh, index, link, override
        self.readonly = set()
        self.refuse = None

    @property
    def material(self):
        return self.mesh.materials[self.index] if self.link == "DATA" else self.override

    @material.setter
    def material(self, value):
        if value is self.refuse and value is not None:
            raise RuntimeError("material setter refused")
        if self.link == "DATA":
            self.mesh.materials[self.index] = value
        else:
            self.override = value

    def is_property_readonly(self, name):
        return name in self.readonly


@pytest.fixture
def trial(monkeypatch, tmp_path):
    tree = NS(
        name="FaceTree",
        bl_idname="ShaderNodeTree",
        nodes=Collection([Node("A"), Node("B")]),
        links=Collection(),
        users=1,
        library=None,
        is_editable=True,
    )
    owner = NS(name="Face", node_tree=tree, library=None, is_editable=True)
    bpy = NS(data=NS(materials=Collection([owner]), is_dirty=False))
    monkeypatch.setitem(sys.modules, "bpy", bpy)
    ref = {"tree_type": "ShaderNodeTree", "owner": {"kind": "MATERIAL", "name": "Face"}}
    target = {"id": "face", "tree_ref": ref, "base_revision": nodes._revision(tree, require_complete=True)}
    captured = []

    def capture(filepath, resolution_x=None, resolution_y=None):
        captured.append([socket.default_value for node in tree.nodes for socket in node.inputs])
        from pathlib import Path

        Path(filepath).write_bytes(b"image")
        return {"success": True, "context": {"width": resolution_x, "height": resolution_y}}

    monkeypatch.setattr(preview, "_capture_viewport", capture)
    return NS(bpy=bpy, tree=tree, target=target, captured=captured, directory=tmp_path / "trial")


def variant(name, value, node="A", target="face"):
    return {
        "id": name,
        "patches": [
            {
                "target": target,
                "operations": [
                    {"op": "set_input", "node": node, "socket": "Value", "value": value},
                ],
            }
        ],
    }


def run(trial, variants, **kwargs):
    return preview.preview_variants(
        targets=[trial.target],
        variants=variants,
        output_directory=str(trial.directory),
        resolution_x=800,
        resolution_y=600,
        **kwargs,
    )


def test_independent_candidates_restore_baseline_and_write_bounded_manifest(trial):
    candidates = [variant("a", 0.2), variant("b", 0.8, node="B")]
    result = run(trial, candidates)
    assert result["success"], result
    assert trial.captured == [[0.2, 0.5], [0.5, 0.8]]
    assert [node.inputs[0].default_value for node in trial.tree.nodes] == [0.5, 0.5]
    context = result["context"]
    assert context["restored"]
    assert context["targets"][0]["revision"] == trial.target["base_revision"]
    manifest = json.loads((trial.directory / "preview-manifest.json").read_text())
    assert manifest["results"][0]["patches"] == candidates[0]["patches"]
    assert manifest["results"][0]["images"][0]["bytes"] == 5
    assert manifest["results"][0]["images"][0]["view"] == "current"
    assert manifest["results"][0]["images"][0]["width"] == 800
    assert "nodes" not in context and "base64" not in json.dumps(context)


def test_empty_patches_capture_baseline_before_candidate_without_writes(trial, monkeypatch):
    execute = nodes._execute
    applied = []

    def apply(tree, operations, undo):
        applied.append(operations)
        execute(tree, operations, undo)

    monkeypatch.setattr(nodes, "_execute", apply)
    candidates = [{"id": "baseline", "patches": []}, variant("changed", 0.2)]
    result = run(trial, candidates)
    assert result["success"], result
    assert trial.captured == [[0.5, 0.5], [0.2, 0.5]]
    assert applied == [candidates[1]["patches"][0]["operations"]]
    assert [node.inputs[0].default_value for node in trial.tree.nodes] == [0.5, 0.5]
    context = result["context"]
    assert context["restored"] and not context["mutated"]
    assert context["targets"][0]["revision"] == trial.target["base_revision"]
    manifest = json.loads((trial.directory / "preview-manifest.json").read_text())
    assert [record["id"] for record in manifest["results"]] == ["baseline", "changed"]
    assert manifest["results"][0]["patches"] == []
    for record in manifest["results"]:
        assert record["status"] == "complete" and record["restored"]
        assert record["images"][0]["path"] == str(trial.directory / (record["id"] + ".png"))


@pytest.mark.parametrize("invalid", ["node", "operations", "targets"])
def test_baseline_is_not_captured_when_later_candidate_or_targets_are_invalid(trial, invalid):
    candidates = [{"id": "baseline", "patches": []}, variant("invalid", 0.2)]
    targets = [trial.target]
    if invalid == "node":
        candidates[1]["patches"][0]["operations"][0]["node"] = "missing"
        error = "Node not found: missing"
    elif invalid == "operations":
        candidates[1]["patches"][0]["operations"] = []
        error = "Each patch requires 1..100 parameter operations"
    else:
        targets = []
        error = "targets must contain 1..16 target objects"
    result = preview.preview_variants(targets, candidates, str(trial.directory))
    assert not result["success"]
    assert result["error"] == error
    assert result["context"]["failure_stage"] == "preflight"
    assert result["context"]["results"] == []
    assert not trial.captured and not trial.directory.exists()
    assert trial.tree.nodes[0].inputs[0].default_value == 0.5


@pytest.mark.parametrize(
    "kind",
    ["stale", "linked", "duplicate-target", "duplicate-variant", "unknown-target", "non-parameter", "invalid-late"],
)
def test_invalid_batch_rejected_before_any_write(trial, kind):
    candidates = [variant("a", 0.2), variant("b", 0.8)]
    targets = [trial.target]
    if kind == "stale":
        trial.target["base_revision"] = "old"
    elif kind == "linked":
        trial.tree.nodes[0].inputs[0].is_linked = True
        trial.target["base_revision"] = nodes._revision(trial.tree, require_complete=True)
    elif kind == "duplicate-target":
        targets = [trial.target, dict(trial.target, id="alias")]
    elif kind == "duplicate-variant":
        candidates[1]["id"] = "a"
    elif kind == "unknown-target":
        candidates[1]["patches"][0]["target"] = "missing"
    elif kind == "non-parameter":
        candidates[1]["patches"][0]["operations"][0] = {"op": "set_label", "node": "A", "value": "label"}
    else:
        candidates[1]["patches"][0]["operations"][0]["node"] = "missing"
    result = preview.preview_variants(targets, candidates, str(trial.directory))
    assert not result["success"], result
    assert result["context"]["failure_stage"] == "preflight"
    assert trial.captured == []
    assert trial.tree.nodes[0].inputs[0].default_value == 0.5
    assert not trial.directory.exists()


@pytest.mark.parametrize("filename", ["a.png", "preview-manifest.json"])
def test_existing_outputs_never_overwritten(trial, filename):
    trial.directory.mkdir()
    destination = trial.directory / filename
    destination.write_bytes(b"keep")
    result = run(trial, [variant("a", 0.2)])
    assert not result["success"]
    assert destination.read_bytes() == b"keep"
    assert not trial.captured


def test_capture_failure_restores_and_returns_already_completed_images(trial, monkeypatch):
    capture = preview._capture_viewport

    def fail_second(**kwargs):
        if len(trial.captured) == 1:
            return {"success": False, "context": {"detail": "GPU capture failed"}}
        return capture(**kwargs)

    monkeypatch.setattr(preview, "_capture_viewport", fail_second)
    result = run(trial, [variant("a", 0.2), variant("b", 0.8)])
    assert not result["success"]
    context = result["context"]
    assert context["failure_stage"] == "capture"
    assert context["restored"]
    assert len(context["results"]) == 2
    assert context["results"][0]["images"][0]["path"].endswith("a.png")
    assert "GPU capture failed" in json.dumps(context)
    assert trial.tree.nodes[0].inputs[0].default_value == 0.5


def test_apply_failure_restores_earlier_writes(trial, monkeypatch):
    execute = nodes._execute

    def failing(tree, operations, undo):
        execute(tree, operations, undo)
        raise RuntimeError("setter failed after applying")

    monkeypatch.setattr(nodes, "_execute", failing)
    result = run(trial, [variant("a", 0.2)])
    assert not result["success"]
    assert result["context"]["failure_stage"] == "apply"
    assert result["context"]["restored"]
    assert not trial.captured
    assert trial.tree.nodes[0].inputs[0].default_value == 0.5


def test_restore_failure_is_not_success_and_stops_next_candidate(trial, monkeypatch):
    def poisoned(tree, operations, undo):
        def cannot_restore():
            raise RuntimeError("restore refused")

        undo.append(cannot_restore)
        tree.nodes[0].inputs[0].default_value = 0.2

    monkeypatch.setattr(nodes, "_execute", poisoned)
    result = run(trial, [variant("a", 0.2), variant("b", 0.8)])
    assert not result["success"]
    context = result["context"]
    assert context["failure_stage"] == "restore"
    assert not context["restored"]
    assert context["mutated"]
    assert len(trial.captured) == 1
    assert "restore refused" in json.dumps(context["restore_errors"])


def test_shared_permission_and_pending_dirty_are_preserved(trial, monkeypatch):
    trial.tree.users = 2
    rejected = run(trial, [variant("a", 0.2)])
    assert not rejected["success"]
    trial.target["allow_shared"] = True
    monkeypatch.setattr(_managed_writes, "_write_revision", 7)
    monkeypatch.setattr(_managed_writes, "_saved_revision", 3)
    trial.bpy.data.is_dirty = True
    accepted = run(trial, [variant("a", 0.2)])
    assert accepted["success"], accepted
    assert _managed_writes.current_revision() == 7
    assert _managed_writes.pending_write()
    assert trial.bpy.data.is_dirty


@pytest.mark.parametrize("value", [1.2, float("nan"), [0.2, 0.3], True])
def test_actual_socket_range_type_and_shape_are_checked_before_capture(trial, value):
    trial.tree.nodes[0].inputs[0].bl_rna.properties = [
        NS(identifier="default_value", type="FLOAT", is_readonly=False, array_length=0, hard_min=0.0, hard_max=1.0)
    ]
    result = run(trial, [variant("a", value)])
    assert not result["success"]
    assert result["context"]["failure_stage"] == "preflight"
    assert not trial.captured


def test_output_parameters_and_multiple_targets_are_restored(trial):
    output = NS(
        name="Output",
        identifier="Output",
        type="VALUE",
        default_value=0.4,
        is_linked=True,
        bl_rna=NS(properties=[]),
    )
    trial.tree.nodes[0].outputs.append(output)
    trial.target["base_revision"] = nodes._revision(trial.tree, require_complete=True)
    second_tree = NS(
        name="OtherTree",
        bl_idname="ShaderNodeTree",
        nodes=Collection([Node("A")]),
        links=Collection(),
        users=1,
        library=None,
        is_editable=True,
    )
    trial.bpy.data.materials.append(NS(name="Other", node_tree=second_tree, library=None, is_editable=True))
    other = {
        "id": "other",
        "tree_ref": {"tree_type": "ShaderNodeTree", "owner": {"kind": "MATERIAL", "name": "Other"}},
        "base_revision": nodes._revision(second_tree, require_complete=True),
    }
    candidate = variant("a", 0.2, target="other")
    candidate["patches"].append(
        {
            "target": "face",
            "operations": [
                {"op": "set_output", "node": "A", "socket": "Output", "value": 0.7},
            ],
        }
    )
    result = preview.preview_variants([trial.target, other], [candidate], str(trial.directory))
    assert result["success"], result
    assert output.default_value == 0.4
    assert second_tree.nodes[0].inputs[0].default_value == 0.5
    assert len(result["context"]["targets"]) == 2


def test_schema_exposes_native_batch_entry():
    from pathlib import Path

    import yaml
    from conftest import load_skill_script

    skill = Path(__file__).resolve().parents[1] / "src/dcc_mcp_blender/skills/blender-node-runtime"
    tools = {tool["name"]: tool for tool in yaml.safe_load((skill / "tools.yaml").read_text())["tools"]}
    tool = tools["preview_variants"]
    assert tool["affinity"] == "main"
    assert tool["read_only"] is False
    assert tool["input_schema"]["required"] == ["targets", "variants", "output_directory"]
    assert callable(load_skill_script("blender-node-runtime", "preview_variants").main)


def test_core_preview_schema_accepts_baseline_and_keeps_nonempty_target_and_operation_bounds(trial, monkeypatch):
    from unittest.mock import Mock

    from conftest import SKILLS_ROOT
    from dcc_mcp_core import create_skill_server
    from jsonschema import Draft202012Validator, ValidationError

    monkeypatch.setenv("DCC_MCP_DISABLE_DEFAULT_SKILL_PATHS", "1")
    server = create_skill_server("blender", extra_paths=[str(SKILLS_ROOT)], accumulated=False)
    executor = Mock(return_value="{}")
    server.set_in_process_executor(executor)
    server.load_skill("blender-node-runtime")
    tools = {tool["name"]: tool for tool in server.get_skill_info("blender-node-runtime")["tools"]}
    schema = tools["preview_variants"]["input_schema"]
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    request = {
        "targets": [trial.target],
        "variants": [{"id": "baseline", "patches": []}, variant("changed", 0.2)],
        "output_directory": str(trial.directory),
    }
    validator.validate(request)
    case = material_target(trial)
    material_request = dict(request, targets=[case.target], variants=[material_variant()])
    validator.validate(material_request)
    validator.validate(dict(material_request, targets=[dict(case.target, base_material=None)]))
    invalid_requests = [
        dict(request, targets=[]),
        dict(request, variants=[{"id": "invalid", "patches": [{"target": "face", "operations": []}]}]),
        dict(material_request, targets=[dict(case.target, material_slot=-1)]),
        dict(material_request, variants=[material_variant(material={"name": "pointer"})]),
    ]
    for invalid in invalid_requests:
        with pytest.raises(ValidationError):
            validator.validate(invalid)
    executor.assert_not_called()


def material_target(trial, link="DATA"):
    original = trial.bpy.data.materials[0]
    dormant = NS(name="Dormant")
    replacement = NS(name="Diagnostic Transparent", library=NS(), is_editable=False, users=3)
    trial.bpy.data.materials.extend([dormant, replacement])
    mesh = NS(materials=[original, dormant], users=2, library=None, is_editable=True)
    slot = MaterialSlot(mesh, 0, link, dormant)
    other_slot = MaterialSlot(mesh, 1, "OBJECT", original)
    obj = NS(
        name="Character",
        type="MESH",
        mode="OBJECT",
        data=mesh,
        material_slots=[slot, other_slot],
        library=None,
        is_editable=True,
    )
    sibling = NS(name="Sibling", material_slots=[MaterialSlot(mesh, 0, "DATA", None)])
    trial.bpy.data.objects = Collection([obj, sibling])
    target = {
        "id": "face5",
        "owner": {"type": "object", "name": obj.name},
        "material_slot": 0,
        "base_link": link,
        "base_material": slot.material.name,
    }
    return NS(
        obj=obj,
        slot=slot,
        mesh=mesh,
        original=original,
        dormant=dormant,
        replacement=replacement,
        sibling=sibling,
        target=target,
    )


def material_variant(name="omit", material="Diagnostic Transparent"):
    return {"id": name, "patches": [{"target": "face5", "operations": [{"op": "set_material", "material": material}]}]}


@pytest.mark.parametrize("link", ["DATA", "OBJECT"])
@pytest.mark.parametrize("failure", [None, "capture", "apply"])
def test_material_slot_override_is_object_local_and_restores_dormant_binding(trial, monkeypatch, link, failure):
    case = material_target(trial, link)
    capture = preview._capture_viewport
    observed = []
    if failure == "apply":
        case.slot.refuse = case.replacement

    def inspect(**kwargs):
        observed.append(case.slot.material)
        if len(observed) == 1:
            assert case.slot.link == link and case.slot.material.name == case.target["base_material"]
        else:
            assert case.slot.link == "OBJECT" and case.slot.material is case.replacement
        assert case.mesh.materials == [case.original, case.dormant]
        assert case.sibling.material_slots[0].material is case.original
        assert case.obj.material_slots[1].material is case.original
        return {"success": False} if failure == "capture" and len(observed) == 2 else capture(**kwargs)

    monkeypatch.setattr(preview, "_capture_viewport", inspect)
    result = preview.preview_variants(
        [case.target],
        [{"id": "baseline", "patches": []}, material_variant(), material_variant("again")],
        str(trial.directory),
    )
    assert result["success"] is (failure is None), result
    assert result["context"]["restored"] and not result["context"]["mutated"]
    assert case.slot.link == link and case.slot.override is case.dormant
    assert case.mesh.materials == [case.original, case.dormant]
    assert len(observed) == (3 if failure is None else 2 if failure == "capture" else 1)
    if failure:
        assert result["context"]["failure_stage"] == failure
    else:
        manifest = json.loads((trial.directory / "preview-manifest.json").read_text())
        assert manifest["targets"][0] == case.target
        assert manifest["results"][1]["patches"] == material_variant()["patches"]
        assert manifest["results"][1]["images"][0]["path"].endswith("omit.png")


def test_preview_schema_accepts_bounded_material_slot_target(trial):
    import yaml
    from conftest import SKILLS_ROOT
    from jsonschema import Draft202012Validator

    case = material_target(trial)
    tool = next(
        tool
        for tool in yaml.safe_load((SKILLS_ROOT / "blender-node-runtime/tools.yaml").read_text())["tools"]
        if tool["name"] == "preview_variants"
    )
    Draft202012Validator(tool["input_schema"]).validate(
        {
            "targets": [case.target],
            "variants": [material_variant()],
            "output_directory": str(trial.directory),
        }
    )


@pytest.mark.parametrize(
    "bad",
    [
        "stale-material",
        "stale-link",
        "index",
        "boolean-index",
        "duplicate",
        "mode",
        "type",
        "linked-object",
        "linked-mesh",
        "readonly-object",
        "readonly-mesh",
        "readonly-link",
        "readonly-material",
        "missing-material",
        "pointer",
        "multiple-operations",
    ],
)
def test_material_batch_preflight_rejects_invalid_late_candidate_without_writes(trial, bad):
    case = material_target(trial)
    targets, candidate = [case.target], material_variant()
    if bad == "stale-material":
        case.target["base_material"] = "old"
    elif bad == "stale-link":
        case.target["base_link"] = "OBJECT"
    elif bad == "index":
        case.target["material_slot"] = 2
    elif bad == "boolean-index":
        case.target["material_slot"] = True
    elif bad == "duplicate":
        targets.append(dict(case.target, id="alias"))
    elif bad == "mode":
        case.obj.mode = "EDIT"
    elif bad == "type":
        case.obj.type = "LIGHT"
    elif bad.startswith("linked-"):
        (case.obj if bad.endswith("object") else case.mesh).library = NS()
    elif bad in {"readonly-object", "readonly-mesh"}:
        (case.obj if bad.endswith("object") else case.mesh).is_editable = False
    elif bad in {"readonly-link", "readonly-material"}:
        case.slot.readonly.add(bad.split("-")[1])
    elif bad == "missing-material":
        candidate = material_variant(material="missing")
    elif bad == "pointer":
        candidate = material_variant(material={"name": "Diagnostic Transparent"})
    else:
        candidate["patches"][0]["operations"] *= 2
    result = preview.preview_variants(targets, [{"id": "baseline", "patches": []}, candidate], str(trial.directory))
    assert not result["success"] and result["context"]["failure_stage"] == "preflight", result
    assert not trial.captured and not trial.directory.exists()
    assert case.slot.link == "DATA" and case.slot.override is case.dormant
    assert case.mesh.materials == [case.original, case.dormant]


@pytest.mark.parametrize("link", ["DATA", "OBJECT"])
def test_material_override_restores_empty_slot_and_empty_dormant_override(trial, link):
    case = material_target(trial, link)
    case.mesh.materials[0] = None
    case.slot.override = None
    case.target["base_material"] = None
    result = preview.preview_variants([case.target], [material_variant()], str(trial.directory))
    assert result["success"], result
    assert result["context"]["targets"][0]["base_material"] is None
    assert case.slot.link == link and case.slot.material is None and case.slot.override is None
    assert case.mesh.materials[0] is None


def test_material_restore_failure_stops_batch_even_when_data_binding_is_visible_again(trial, monkeypatch):
    case = material_target(trial)
    capture = preview._capture_viewport

    def poison(**kwargs):
        case.slot.refuse = case.dormant
        return capture(**kwargs)

    monkeypatch.setattr(preview, "_capture_viewport", poison)
    result = preview.preview_variants(
        [case.target], [material_variant(), material_variant("later")], str(trial.directory)
    )
    assert not result["success"]
    assert result["context"]["failure_stage"] == "restore"
    assert not result["context"]["restored"] and result["context"]["mutated"]
    assert len(trial.captured) == 1
    assert case.slot.link == "DATA" and case.slot.material is case.original
    assert case.slot.override is case.replacement


@pytest.mark.parametrize("blocked", ["DATA", "OBJECT"])
def test_material_link_setter_readback_prevents_shared_mesh_write_and_false_restore(trial, blocked):
    case = material_target(trial)

    class StickySlot(MaterialSlot):
        def __setattr__(self, name, value):
            if name == "link" and getattr(self, "blocked", None) == value:
                return
            super().__setattr__(name, value)

    case.obj.material_slots[0] = slot = StickySlot(case.mesh, 0, "DATA", case.dormant)
    slot.blocked = blocked
    result = preview.preview_variants(
        [case.target], [material_variant(), material_variant("later")], str(trial.directory)
    )
    assert not result["success"]
    assert case.mesh.materials == [case.original, case.dormant]
    if blocked == "OBJECT":
        assert result["context"]["failure_stage"] == "apply" and result["context"]["restored"]
        assert not trial.captured
    else:
        assert result["context"]["failure_stage"] == "restore" and not result["context"]["restored"]
        assert len(trial.captured) == 1


def test_material_slot_targets_mix_with_node_and_rna_targets(trial, monkeypatch):
    light, light_target = rna_target(trial)
    light_obj = trial.bpy.data.objects[0]
    case = material_target(trial)
    trial.bpy.data.objects.append(light_obj)
    capture = preview._capture_viewport

    def inspect(**kwargs):
        assert light.energy == 0.0
        assert trial.tree.nodes[0].inputs[0].default_value == 0.2
        assert case.slot.material is case.replacement
        return capture(**kwargs)

    monkeypatch.setattr(preview, "_capture_viewport", inspect)
    candidate = material_variant()
    candidate["patches"] += variant("node", 0.2)["patches"]
    candidate["patches"].append({"target": "light", "operations": [{"op": "set_property", "value": 0.0}]})
    result = preview.preview_variants([case.target, trial.target, light_target], [candidate], str(trial.directory))
    assert result["success"] and result["context"]["restored"], result
    assert light.energy == 10.0 and trial.tree.nodes[0].inputs[0].default_value == 0.5
    assert case.slot.link == "DATA" and case.slot.override is case.dormant


def rna_target(trial):
    prop = NS(
        identifier="energy",
        type="FLOAT",
        is_readonly=False,
        is_runtime=False,
        is_array=False,
        hard_min=0.0,
        hard_max=100.0,
    )
    light = NS(energy=10.0, bl_rna=NS(properties={"energy": prop}))
    obj = NS(name="Light", type="LIGHT", data=light, bl_rna=NS(properties={"data": NS(type="POINTER")}))
    trial.bpy.data.objects = Collection([obj])
    return light, {
        "id": "light",
        "owner": {"type": "object", "name": "Light"},
        "path": "data.energy",
        "base_value": 10.0,
    }


@pytest.mark.parametrize("failure", [False, True])
def test_rna_trials_restore_each_baseline_even_when_capture_fails(trial, monkeypatch, failure):
    light, target = rna_target(trial)
    observed = []
    old = preview._capture_viewport

    def capture(**kwargs):
        observed.append(light.energy)
        return {"success": False} if failure else old(**kwargs)

    monkeypatch.setattr(preview, "_capture_viewport", capture)
    candidates = [
        {"id": str(i), "patches": [{"target": "light", "operations": [{"op": "set_property", "value": value}]}]}
        for i, value in enumerate([0.0, 20.0])
    ]
    result = preview.preview_variants([target], candidates, str(trial.directory))
    assert result["success"] is not failure, result
    assert result["context"]["restored"]
    assert light.energy == 10.0
    assert observed == ([0.0] if failure else [0.0, 20.0])


@pytest.mark.parametrize("bad", ["stale", "range", "runtime", "duplicate"])
def test_rna_preflight_rejects_before_mutation(trial, bad):
    light, target = rna_target(trial)
    targets = [target]
    value = 20.0
    if bad == "stale":
        target["base_value"] = 9.0
    elif bad == "range":
        value = 101.0
    elif bad == "runtime":
        light.bl_rna.properties["energy"].is_runtime = True
    else:
        targets.append(dict(target, id="alias"))
    result = preview.preview_variants(
        targets,
        [{"id": "one", "patches": [{"target": "light", "operations": [{"op": "set_property", "value": value}]}]}],
        str(trial.directory),
    )
    assert not result["success"]
    assert result["context"]["failure_stage"] == "preflight"
    assert light.energy == 10.0
    assert not trial.captured


def test_rna_restore_readback_failure_stops_batch(trial, monkeypatch):
    light, target = rna_target(trial)

    def capture(**kwargs):
        # Emulate a native setter that no longer restores its original value.
        light.bl_rna.properties["energy"].hard_max = 5.0
        return {"success": False}

    monkeypatch.setattr(preview, "_capture_viewport", capture)
    result = preview.preview_variants(
        [target],
        [{"id": "one", "patches": [{"target": "light", "operations": [{"op": "set_property", "value": 0.0}]}]}],
        str(trial.directory),
    )
    assert not result["success"]
    assert not result["context"]["restored"]
    assert result["context"]["mutated"]


@pytest.mark.parametrize("fail_capture", [False, True])
def test_existing_output_link_isolation_restores_links_and_mixed_rna(trial, monkeypatch, fail_capture):
    from test_node_runtime_ops import Links

    light, rna_spec = rna_target(trial)
    a, b = trial.tree.nodes
    c = Node("C")
    trial.tree.nodes.append(c)
    for node in (a, b, c):
        node.outputs.append(NS(name="Out", identifier="Out", node=node, type="VALUE", bl_idname="NodeSocketFloat"))
        node.inputs[0].node = node
    trial.tree.links = Links()
    trial.tree.links.new(a.outputs[0], b.inputs[0])
    trial.target["base_revision"] = nodes._revision(trial.tree, require_complete=True)
    original_capture = preview._capture_viewport

    def capture(**kwargs):
        assert light.energy == 0.0
        assert trial.tree.links[0].from_node is c
        return {"success": False} if fail_capture else original_capture(**kwargs)

    monkeypatch.setattr(preview, "_capture_viewport", capture)
    operations = [
        {"op": "disconnect", "from_node": "A", "from_socket": "Out", "to_node": "B", "to_socket": "Value"},
        {"op": "connect", "from_node": "C", "from_socket": "Out", "to_node": "B", "to_socket": "Value"},
    ]
    result = preview.preview_variants(
        [trial.target, rna_spec],
        [
            {
                "id": "isolated",
                "patches": [
                    {"target": "face", "operations": operations},
                    {"target": "light", "operations": [{"op": "set_property", "value": 0.0}]},
                ],
            }
        ],
        str(trial.directory),
    )
    assert result["success"] is not fail_capture, result
    assert result["context"]["restored"]
    assert light.energy == 10.0
    assert len(trial.tree.links) == 1 and trial.tree.links[0].from_node is a


@pytest.mark.parametrize("path", ["name", "data.type", "use_nodes", "modifiers.clear()"])
def test_structural_rna_paths_are_not_preview_operations(trial, path):
    _, target = rna_target(trial)
    target["path"] = path
    result = preview.preview_variants(
        [target],
        [{"id": "bad", "patches": [{"target": "light", "operations": [{"op": "set_property", "value": 0.0}]}]}],
        str(trial.directory),
    )
    assert not result["success"]
    assert result["context"]["failure_stage"] == "preflight"
    assert not trial.captured


@pytest.mark.parametrize("kind", ["visibility", "color", "modifier"])
def test_supported_native_property_families_restore_short_values(trial, monkeypatch, kind):
    light, target = rna_target(trial)
    obj = trial.bpy.data.objects[0]
    if kind == "visibility":
        parent, path, name, baseline, value, prop_type = obj, "hide_render", "hide_render", False, True, "BOOLEAN"
    elif kind == "color":
        parent, path, name, baseline, value, prop_type = (
            light,
            "data.color",
            "color",
            [1.0, 0.5, 0.25],
            [0.0, 0.0, 0.0],
            "FLOAT",
        )
    else:
        parent = NS(type="DISPLACE", bl_rna=NS(properties={}))
        obj.modifiers = {"Hull": parent}
        obj.bl_rna.properties["modifiers"] = NS(type="COLLECTION")
        path, name, baseline, value, prop_type = 'modifiers["Hull"].strength', "strength", 0.1, 0.0, "FLOAT"
    setattr(parent, name, baseline)
    parent.bl_rna.properties[name] = NS(
        identifier=name,
        type=prop_type,
        is_readonly=False,
        is_runtime=False,
        is_array=kind == "color",
        array_length=3 if kind == "color" else 0,
    )
    target.update(path=path, base_value=baseline)
    old_capture = preview._capture_viewport

    def capture(**kwargs):
        assert getattr(parent, name) == value
        return old_capture(**kwargs)

    monkeypatch.setattr(preview, "_capture_viewport", capture)
    result = preview.preview_variants(
        [target],
        [{"id": "case", "patches": [{"target": "light", "operations": [{"op": "set_property", "value": value}]}]}],
        str(trial.directory),
    )
    assert result["success"], result
    assert getattr(parent, name) == baseline


def test_shared_light_data_is_rejected_without_affecting_other_light_users(trial):
    light, target = rna_target(trial)
    light.users = 2
    result = preview.preview_variants(
        [target],
        [{"id": "off", "patches": [{"target": "light", "operations": [{"op": "set_property", "value": 0.0}]}]}],
        str(trial.directory),
    )
    assert not result["success"]
    assert result["context"]["failure_stage"] == "preflight"
    assert light.energy == 10.0
    assert not trial.captured


@pytest.mark.parametrize("case", ["muted", "limit", "restore-muted"])
def test_link_isolation_preserves_muting_and_other_consumers(trial, monkeypatch, case):
    from test_node_runtime_ops import Links

    a, b = trial.tree.nodes
    c = Node("C")
    trial.tree.nodes.append(c)
    for node in (a, b, c):
        node.outputs.append(
            NS(name="Out", identifier="Out", node=node, type="VALUE", bl_idname="NodeSocketFloat", link_limit=1)
        )
        node.inputs[0].node = node
    trial.tree.links = Links()
    link = trial.tree.links.new(a.outputs[0], b.inputs[0])
    link.is_muted = case == "muted"
    operations = [{"op": "disconnect", "from_node": "A", "from_socket": "Out", "to_node": "B", "to_socket": "Value"}]
    if case == "limit":
        operations = [{"op": "connect", "from_node": "A", "from_socket": "Out", "to_node": "C", "to_socket": "Value"}]
    trial.target["base_revision"] = nodes._revision(trial.tree, require_complete=True)
    if case == "restore-muted":
        original = trial.tree.links.new

        def new(source, target):
            restored = original(source, target)
            restored.is_muted = True
            return restored

        monkeypatch.setattr(trial.tree.links, "new", new)
    result = run(trial, [{"id": "isolate", "patches": [{"target": "face", "operations": operations}]}])
    assert not result["success"]
    if case == "restore-muted":
        assert result["context"]["failure_stage"] == "restore"
        assert result["context"]["mutated"]
    else:
        assert result["context"]["failure_stage"] == "preflight"
        assert not trial.captured
        assert len(trial.tree.links) == 1 and trial.tree.links[0] is link


@pytest.mark.parametrize("case", ["disconnect", "occupied", "limit"])
def test_link_preflight_matches_distinct_rna_socket_wrappers(monkeypatch, case):
    class Socket(NS):
        def __eq__(self, other):
            return isinstance(other, Socket) and self.as_pointer() == other.as_pointer()

        def as_pointer(self):
            return self.pointer

    source = Socket(pointer=1, link_limit=1)
    target = Socket(pointer=2)
    linked_source = Socket(pointer=1, link_limit=1)
    linked_target = Socket(pointer=2)
    assert source is not linked_source and source == linked_source
    assert target is not linked_target and target == linked_target
    links = [(linked_source, linked_target, False)]
    operation = {
        "op": "disconnect" if case == "disconnect" else "connect",
        "from_node": "A",
        "from_socket": "Out",
        "to_node": "B",
        "to_socket": "Value",
    }
    if case == "occupied":
        source = Socket(pointer=3, link_limit=1)
        operation["from_node"] = "C"
    elif case == "limit":
        target = Socket(pointer=3)
        operation["to_node"] = "C"
    monkeypatch.setattr(
        nodes, "_socket", lambda tree, node, socket, direction: source if direction == "OUTPUT" else target
    )
    if case == "disconnect":
        preview._link_operation(None, operation, links)
        assert links == []
    else:
        message = "Disconnect the occupied input" if case == "occupied" else "Source link limit"
        with pytest.raises(ValueError, match=message):
            preview._link_operation(None, operation, links)
        assert len(links) == 1
        assert links[0][0] is linked_source and links[0][1] is linked_target
