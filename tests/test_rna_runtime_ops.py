"""Public live RNA skill contracts against a strict Blender boundary fake."""

from types import SimpleNamespace
from unittest.mock import patch

from tests.test_skills_rna import Properties, property_info


class Operator:
    def __init__(self, status=None):
        self.status = status or {"FINISHED"}
        self.executions = []
        self.allowed = True

    def get_rna_type(self):
        return SimpleNamespace(
            identifier="MMD_TOOLS_OT_import_model",
            name="Import PMX",
            description="",
            properties=Properties([property_info("filepath", "STRING")]),
        )

    def poll(self):
        return self.allowed

    def __call__(self, execution, **parameters):
        self.executions.append((execution, parameters))
        return self.status


def host(op=None):
    return SimpleNamespace(
        app=SimpleNamespace(version_string="5.2.0"),
        ops=SimpleNamespace(mmd_tools=SimpleNamespace(import_model=op or Operator())),
        context=SimpleNamespace(active_object=None, selected_objects=[], mode="OBJECT", area=None, window=None),
    )


def test_operator_rejects_unknown_fields_and_returns_actual_status():
    from dcc_mcp_blender._rna_runtime_ops import call_operator

    op = Operator({"CANCELLED"})
    with patch.dict("sys.modules", {"bpy": host(op)}):
        rejected = call_operator("mmd_tools.import_model", {"not_a_field": 1})
        assert rejected["success"] is False
        assert op.executions == []
        result = call_operator("mmd_tools.import_model", {"filepath": "synthetic.pmx"})
        assert result["success"] is False
        assert result["context"]["operator_status"] == ["CANCELLED"]
        assert op.executions == [("EXEC_DEFAULT", {"filepath": "synthetic.pmx"})]


def test_live_operator_search_and_description_need_no_cached_catalog():
    from dcc_mcp_blender._rna_runtime_ops import describe_operator, search_operators

    bpy = host()
    with patch.dict("sys.modules", {"bpy": bpy}):
        search = search_operators(query="import", limit=1)
        assert search["context"]["operators"][0]["operator_id"] == "mmd_tools.import_model"
        assert search["context"]["operators"][0]["source_module"] is None
        schema = describe_operator("mmd_tools.import_model")["context"]
        assert schema["properties"][0]["identifier"] == "filepath"
        assert schema["properties"][0]["type"] == "STRING"
        bpy.ops.mmd_tools.another_import = Operator()
        assert search_operators(query="import")["context"]["total"] == 2


def test_property_group_search_quality_and_validated_write_readback():
    from dcc_mcp_blender._rna_runtime_ops import get_properties, set_property

    quality = property_info("iterations", name="Quality", hard_min=1, hard_max=32)
    group = SimpleNamespace(bl_rna=SimpleNamespace(properties=Properties([quality])), iterations=4)
    scene = SimpleNamespace(
        bl_rna=SimpleNamespace(properties=Properties([property_info("wiggle", "POINTER")])), wiggle=group
    )
    bpy = host()
    bpy.context.scene = scene
    with patch.dict("sys.modules", {"bpy": bpy}):
        result = get_properties({"type": "scene"}, path="wiggle", query="Quality")
        assert result["context"]["properties"][0]["value"] == 4
        assert set_property({"type": "scene"}, "wiggle.iterations", 8)["context"]["value"] == 8
        assert set_property({"type": "scene"}, "wiggle.iterations", True)["success"] is False
        assert set_property({"type": "scene"}, "wiggle.iterations", 99)["success"] is False
        assert get_properties({"type": "scene"}, path="wiggle.__class__")["success"] is False
        assert get_properties({"type": "scene"}, path="wiggle.iterations()")["success"] is False


def test_operator_reports_modal_poll_and_missing_context_without_retries():
    from dcc_mcp_blender._rna_runtime_ops import call_operator

    op = Operator({"RUNNING_MODAL"})
    with patch.dict("sys.modules", {"bpy": host(op)}):
        modal = call_operator("mmd_tools.import_model")
        assert modal["success"] is False
        assert modal["context"]["operator_status"] == ["RUNNING_MODAL"]
        assert modal["context"]["completed"] is False
        op.allowed = False
        assert call_operator("mmd_tools.import_model")["message"] == "Operator poll failed"
        assert len(op.executions) == 1
        op.allowed = True
        assert call_operator("mmd_tools.import_model", context={"area_type": "VIEW_3D"})["success"] is False
        assert call_operator("mmd_tools.import_model", context={"active_object": "Cube"})["success"] is False
        assert call_operator("mmd_tools.import_model", context={"selected_objects": ["Cube"]})["success"] is False
        assert call_operator("mmd_tools.import_model", context={"mode": "EDIT_MESH"})["success"] is False
        assert call_operator("mmd_tools.import_model", context={"invented": True})["success"] is False
        assert len(op.executions) == 1


def test_operators_reject_wrong_types_complex_values_and_typed_lifecycle_bypass():
    from dcc_mcp_blender._rna_runtime_ops import call_operator

    op = Operator()
    bpy = host(op)
    bpy.ops.wm = SimpleNamespace(open_mainfile=op, save_mainfile=op, quit_blender=op)
    bpy.ops.preferences = SimpleNamespace(addon_enable=op)
    with patch.dict("sys.modules", {"bpy": bpy}):
        assert call_operator("mmd_tools.import_model", {"filepath": 5})["success"] is False
        assert call_operator("mmd_tools.import_model", {"filepath": "x" * 4097})["success"] is False
        for operator_id in ("wm.open_mainfile", "wm.save_mainfile", "wm.quit_blender", "preferences.addon_enable"):
            assert call_operator(operator_id)["success"] is False
        op.get_rna_type = lambda: SimpleNamespace(properties=Properties([property_info("files", "COLLECTION")]))
        assert call_operator("mmd_tools.import_model", {"files": []})["success"] is False
        assert op.executions == []


def test_owner_collection_access_is_rna_only_and_never_evaluates_expressions():
    from dcc_mcp_blender._rna_runtime_ops import get_properties, set_property

    prop = property_info("iterations", hard_min=1, hard_max=32)
    group = SimpleNamespace(bl_rna=SimpleNamespace(properties=Properties([prop])), iterations=4)
    obj = SimpleNamespace(
        bl_rna=SimpleNamespace(properties=Properties([property_info("groups", "COLLECTION")])), groups={"rig": group}
    )
    bpy = host()
    bpy.data = SimpleNamespace(objects={"Rig": obj}, materials={"Toon": group}, scenes={"Scene": group})
    bpy.context.preferences = SimpleNamespace(addons={"wiggle": SimpleNamespace(preferences=group)})
    obj.pose = SimpleNamespace(bones={"Hair": group})
    with patch.dict("sys.modules", {"bpy": bpy}):
        for owner in ({"type": "object", "name": "Rig"},):
            assert set_property(owner, 'groups["rig"].iterations', 7)["context"]["value"] == 7
            assert get_properties(owner, 'groups["rig"].iterations')["context"]["value"] == 7
            for path in (
                'groups["rig"].__dict__',
                'groups["rig"].iterations()',
                "groups[other].iterations",
                'groups["rig"].iterations + 1',
            ):
                assert get_properties(owner, path)["success"] is False
        for owner in (
            {"type": "material", "name": "Toon"},
            {"type": "scene", "name": "Scene"},
            {"type": "pose_bone", "object_name": "Rig", "name": "Hair"},
            {"type": "addon_preferences", "name": "wiggle"},
        ):
            assert get_properties(owner, "iterations")["context"]["value"] == 7
        assert set_property({"type": "object", "name": "Rig"}, "groups", {})["success"] is False


def test_live_access_requires_main_thread():
    from concurrent.futures import ThreadPoolExecutor

    from dcc_mcp_blender._rna_runtime_ops import call_operator

    with patch.dict("sys.modules", {"bpy": host()}), ThreadPoolExecutor(max_workers=1) as executor:
        assert executor.submit(call_operator, "mmd_tools.import_model").result()["success"] is False


def test_builtin_tools_are_main_thread_and_call_the_public_boundary():
    from pathlib import Path

    import yaml

    from tests.conftest import load_and_call

    skill = Path(__file__).resolve().parents[1] / "src/dcc_mcp_blender/skills/blender-rna-runtime"
    tools = yaml.safe_load((skill / "tools.yaml").read_text())["tools"]
    assert {tool["name"] for tool in tools} == {
        "search_operators",
        "describe_operator",
        "get_properties",
        "set_property",
        "call_operator",
    }
    for tool in tools:
        assert tool["affinity"] == "main"
        assert tool["read_only"] is (tool["name"] not in {"set_property", "call_operator"})
    result = load_and_call(
        "blender-rna-runtime/scripts/call_operator.py",
        host(),
        operator_id="mmd_tools.import_model",
        parameters={"filepath": "synthetic.pmx"},
    )
    assert result["context"]["operator_status"] == ["FINISHED"]
    assert result["context"]["business_result_verified"] is False


def test_operator_validates_enum_numeric_range_arrays_and_boolean_distinction():
    from dcc_mcp_blender._rna_runtime_ops import call_operator

    op = Operator()
    properties = Properties(
        [
            property_info("count", hard_min=1, hard_max=4),
            property_info("strength", "FLOAT", hard_min=0, hard_max=1),
            property_info("direction", "FLOAT", is_array=True, array_length=3),
            property_info("mode", "ENUM", enum_items_static=[SimpleNamespace(identifier="TEMPORAL")]),
        ]
    )
    op.get_rna_type = lambda: SimpleNamespace(properties=properties)
    with patch.dict("sys.modules", {"bpy": host(op)}):
        for parameters in (
            {"count": True},
            {"count": 16},
            {"strength": float("nan")},
            {"direction": [0, 1]},
            {"mode": "STABLE"},
        ):
            assert call_operator("mmd_tools.import_model", parameters)["success"] is False
        assert op.executions == []
        assert call_operator("mmd_tools.import_model", {"count": 4, "mode": "TEMPORAL"})["success"] is True
