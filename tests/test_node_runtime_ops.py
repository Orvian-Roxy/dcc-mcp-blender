"""Public node runtime seam, independent of live Blender."""

from __future__ import annotations

import copy
import sys
from types import SimpleNamespace as NS

import pytest

from dcc_mcp_blender import _node_runtime_ops as runtime


class Collection(list):
    def get(self, name):
        if not isinstance(name, str):
            raise TypeError("Blender collection.get requires a string key")
        return next((item for item in self if item.name == name), None)


class Node:
    __module__ = "bpy.types"

    def __init__(self, name):
        self.name = name
        self.label = "human label"
        self.location = [0.0, 0.0]
        self.bl_idname = "ShaderNodeValue"
        self.type = "VALUE"
        self.bl_rna = NS(properties=[])
        self.inputs = Collection(
            [
                NS(
                    name="Value",
                    identifier="Value",
                    type="VALUE",
                    bl_idname="NodeSocketFloat",
                    default_value=0.5,
                    is_linked=False,
                    bl_rna=NS(properties=[]),
                )
            ]
        )
        self.outputs = Collection()


@pytest.fixture
def host(monkeypatch):
    tree = NS(
        name="MaterialTree",
        bl_idname="ShaderNodeTree",
        nodes=Collection([Node("A"), Node("B")]),
        links=Collection(),
        users=1,
        library=None,
        is_editable=True,
    )
    owner = NS(name="Face", node_tree=tree, library=None, is_editable=True, users=1)
    materials = Collection([owner])

    def duplicate(source):
        result = copy.deepcopy(source)
        result.name = ".validation"
        result.copy = lambda: duplicate(result)
        materials.append(result)
        return result

    owner.copy = lambda: duplicate(owner)
    data = NS(
        materials=materials,
        worlds=Collection(),
        lights=Collection(),
        scenes=Collection(),
        node_groups=Collection(),
        is_dirty=False,
    )
    bpy = NS(data=data)
    monkeypatch.setitem(sys.modules, "bpy", bpy)
    return bpy, owner, {"tree_type": "ShaderNodeTree", "owner": {"kind": "MATERIAL", "name": "Face"}}


def test_local_query_pages_and_target_identity(host):
    _, _, ref = host
    first = runtime.query_nodes(ref, limit=1)
    assert first["success"]
    result = first["context"]
    assert result["tree_ref"] == ref
    assert [node["name"] for node in result["nodes"]] == ["A"]
    assert result["next_offset"] == 1
    second = runtime.query_nodes(ref, offset=1, limit=1)
    assert [node["name"] for node in second["context"]["nodes"]] == ["B"]
    assert second["context"]["revision"] == result["revision"]


def test_schema_reads_existing_dynamic_socket_without_probes(host):
    bpy, owner, ref = host
    prop = NS(identifier="default_value", type="FLOAT", is_readonly=False, array_length=0, hard_min=0.0, hard_max=1.0)
    owner.node_tree.nodes[0].inputs[0].bl_rna.properties = [prop]
    schema = runtime.node_schema(ref, "A")
    assert schema["success"]
    socket = next(row for row in schema["context"]["items"] if row["kind"] == "INPUT")
    assert socket["identifier"] == "Value"
    assert socket["value_schema"]["max"] == 1.0
    assert len(bpy.data.materials) == 1
    assert not bpy.data.is_dirty


def test_candidate_keeps_original_and_stale_apply_rejects(host):
    bpy, owner, ref = host
    revision = runtime.query_nodes(ref)["context"]["revision"]
    ops = [{"op": "set_input", "node": "A", "socket": "Value", "value": 0.8}]
    validation = runtime.patch_nodes(ref, revision, ops, action="validate")
    assert validation["success"]
    assert validation["context"]["status"] == "validated"
    assert owner.node_tree.nodes[0].inputs[0].default_value == 0.5
    assert len(bpy.data.materials) == 1
    owner.node_tree.nodes[1].label = "human edit"
    rejected = runtime.patch_nodes(ref, revision, ops)
    assert not rejected["success"]
    assert rejected["context"]["status"] == "stale_revision"
    assert owner.node_tree.nodes[0].inputs[0].default_value == 0.5


class Links(Collection):
    def new(self, source, target):
        link = NS(from_node=source.node, from_socket=source, to_node=target.node, to_socket=target, is_valid=True)
        self.append(link)
        target.is_linked = True
        return link

    def remove(self, link):
        super().remove(link)
        link.to_socket.is_linked = False


def test_connect_readback_and_commit_failure_restore_original(host):
    _, owner, ref = host
    tree = owner.node_tree
    source = NS(name="Out", identifier="Out", type="VALUE", default_value=0.0, is_linked=False)
    source.node = tree.nodes[0]
    tree.nodes[0].outputs.append(source)
    target = tree.nodes[1].inputs[0]
    target.node = tree.nodes[1]
    tree.links = Links()
    revision = runtime.query_nodes(ref)["context"]["revision"]
    result = runtime.patch_nodes(
        ref, revision, [{"op": "connect", "from_node": "A", "from_socket": "Out", "to_node": "B", "to_socket": "Value"}]
    )
    assert result["success"], result
    assert runtime.query_nodes(ref)["context"]["links"][0]["to_node"] == "B"

    class CommitOnlyFailure(Node):
        __module__ = "bpy.types"

        @property
        def label(self):
            return getattr(self, "_label", "human label")

        @label.setter
        def label(self, value):
            if value == "fail" and getattr(self, "reject_commit", False):
                raise RuntimeError("actual commit failed")
            self._label = value

        def __deepcopy__(self, memo):
            result = Node(self.name)
            result.label = self.label
            return result

    tree.nodes.append(CommitOnlyFailure("Failing"))
    tree.nodes[-1].reject_commit = True
    revision = runtime.query_nodes(ref)["context"]["revision"]
    result = runtime.patch_nodes(
        ref,
        revision,
        [
            {"op": "disconnect", "from_node": "A", "from_socket": "Out", "to_node": "B", "to_socket": "Value"},
            {"op": "set_label", "node": "Failing", "value": "fail"},
        ],
    )
    assert not result["success"]
    assert result["context"]["rollback_complete"], result
    assert runtime.query_nodes(ref)["context"]["revision"] == revision


def test_shared_scope_and_geometry_are_explicit(host):
    bpy, owner, ref = host
    owner.node_tree.users = 2
    revision = runtime.query_nodes(ref)["context"]["revision"]
    ops = [{"op": "set_label", "node": "A", "value": "changed"}]
    assert not runtime.patch_nodes(ref, revision, ops)["success"]
    assert not runtime.patch_nodes(ref, revision, ops, allow_shared="false")["success"]
    assert runtime.patch_nodes(ref, revision, ops, allow_shared=True)["success"]
    group = owner.node_tree
    group.name = "Geometry"
    group.bl_idname = "GeometryNodeTree"
    bpy.data.node_groups.append(group)
    geometry = {"tree_type": "GeometryNodeTree", "owner": {"kind": "NODE_GROUP", "name": "Geometry"}}
    assert not runtime.query_nodes(geometry)["context"]["capabilities"]["patch"]
    assert not runtime.patch_nodes(geometry, revision, ops, allow_shared=True)["success"]


def test_incomplete_rollback_is_reported_as_mutated(host):
    _, owner, ref = host

    class PoisonedSocket:
        name = identifier = "Value"
        type = "VALUE"
        is_linked = False
        bl_rna = NS(properties=[])
        value = 0.5
        touched = False

        @property
        def default_value(self):
            return self.value

        @default_value.setter
        def default_value(self, value):
            if self.touched and value == 0.5:
                raise RuntimeError("host refused rollback")
            self.touched = True
            self.value = value

        def __deepcopy__(self, memo):
            return NS(
                name="Value",
                identifier="Value",
                type="VALUE",
                default_value=0.5,
                is_linked=False,
                bl_rna=NS(properties=[]),
            )

    # A later native write fails only on the original; validation uses the copy.
    class FailingNode(Node):
        __module__ = "bpy.types"

        @property
        def label(self):
            return "human label"

        @label.setter
        def label(self, value):
            if value == "fail":
                raise RuntimeError("native setter rejected commit")

        def __deepcopy__(self, memo):
            return Node(self.name)

    owner.node_tree.nodes[0].inputs[0] = PoisonedSocket()
    owner.node_tree.nodes.append(FailingNode("Failing"))
    revision = runtime.query_nodes(ref)["context"]["revision"]
    result = runtime.patch_nodes(
        ref,
        revision,
        [
            {"op": "set_input", "node": "A", "socket": "Value", "value": 0.8},
            {"op": "set_label", "node": "Failing", "value": "fail"},
        ],
    )
    assert not result["success"]
    assert result["context"]["status"] == "rollback_incomplete"
    assert result["context"]["mutated"]
    assert not result["context"]["rollback_complete"]
    assert result["context"]["rollback_errors"]


@pytest.mark.parametrize("replacement_pointer", [22, 11])
def test_image_id_replacement_rejects_old_patch_even_when_name_matches(host, replacement_pointer):
    _, owner, ref = host

    class Image:
        name = name_full = "SharedTexture"
        library = None

        def __init__(self, pointer, uid):
            self.pointer = pointer
            self.session_uid = uid
            self.id_data = self

        def as_pointer(self):
            return self.pointer

    node = owner.node_tree.nodes[0]
    node.image = Image(11, 100)
    node.bl_rna.properties = [NS(identifier="image", type="POINTER", is_readonly=False)]
    before = runtime.query_nodes(ref)["context"]["revision"]
    node.image = Image(replacement_pointer, 200)
    result = runtime.patch_nodes(ref, before, [{"op": "set_label", "node": "B", "value": "changed"}])
    assert not result["success"]
    assert result["context"]["status"] == "stale_revision", result
    assert owner.node_tree.nodes[1].label == "human label"


@pytest.mark.parametrize("field", ["color_ramp", "mapping"])
def test_nested_node_data_edit_rejects_old_patch(host, field):
    _, owner, ref = host
    node = owner.node_tree.nodes[0]
    if field == "color_ramp":
        value = NS(
            color_mode="RGB",
            hue_interpolation="NEAR",
            interpolation="LINEAR",
            elements=[NS(position=0.0, color=[0.0, 0.0, 0.0, 1.0])],
        )

        def edit():
            value.elements[0].color[0] = 1.0
    else:
        value = NS(curves=[NS(points=[NS(location=[0.0, 0.0], handle_type="AUTO")])])

        def edit():
            value.curves[0].points[0].location[1] = 0.5

    setattr(node, field, value)
    node.bl_rna.properties = [NS(identifier=field, type="POINTER", is_readonly=True)]
    before = runtime.query_nodes(ref)["context"]["revision"]
    edit()
    result = runtime.patch_nodes(ref, before, [{"op": "set_label", "node": "B", "value": "changed"}])
    assert not result["success"]
    assert result["context"]["status"] == "stale_revision", result


def test_shared_group_contents_change_rejects_old_parent_patch(host):
    _, owner, ref = host
    node = owner.node_tree.nodes[0]
    node.node_tree = NS(name="Shared", nodes=Collection([Node("Inner")]), links=Collection(), users=2)
    before = runtime.query_nodes(ref)["context"]["revision"]
    node.node_tree.nodes[0].label = "other material author changed shared group"
    result = runtime.patch_nodes(ref, before, [{"op": "set_label", "node": "B", "value": "changed"}])
    assert not result["success"]
    assert result["context"]["status"] == "stale_revision", result


def test_unqualified_nested_storage_keeps_inspection_but_refuses_patch(host):
    _, owner, ref = host
    node = owner.node_tree.nodes[0]
    node.unqualified = NS(secret_data=[1, 2])
    node.bl_rna.properties = [NS(identifier="unqualified", type="POINTER", is_readonly=True)]
    before = runtime.query_nodes(ref)
    assert before["success"]
    assert runtime.node_schema(ref, "A")["success"]
    result = runtime.patch_nodes(
        ref, before["context"]["revision"], [{"op": "set_label", "node": "B", "value": "changed"}]
    )
    assert not result["success"]
    assert "revision support" in result["context"]["detail"]
    assert owner.node_tree.nodes[1].label == "human label"


def test_empty_node_panel_states_does_not_block_native_patch(host):
    _, owner, ref = host
    node = owner.node_tree.nodes[0]
    node.panel_states = []
    node.bl_rna.properties = [NS(identifier="panel_states", type="COLLECTION", is_readonly=True)]
    before = runtime.query_nodes(ref)["context"]["revision"]
    result = runtime.patch_nodes(ref, before, [{"op": "set_label", "node": "B", "value": "changed"}])
    assert result["success"], result


def test_native_node_panel_state_is_fingerprinted_with_readonly_identifier(host):
    _, owner, ref = host
    node = owner.node_tree.nodes[0]
    node.panel_states = [NS(identifier=9, is_collapsed=True), NS(identifier=11, is_collapsed=True)]
    node.bl_rna.properties = [NS(identifier="panel_states", type="COLLECTION", is_readonly=True)]
    patch = [{"op": "set_label", "node": "B", "value": "changed"}]
    before = runtime.query_nodes(ref)["context"]["revision"]
    validation = runtime.patch_nodes(ref, before, patch, action="validate")
    assert validation["success"], validation
    node.panel_states[0].is_collapsed = False
    rejected = runtime.patch_nodes(ref, before, patch)
    assert rejected["context"]["status"] == "stale_revision", rejected
    node.panel_states[0].is_collapsed = True
    node.panel_states[0].identifier = 18
    rejected = runtime.patch_nodes(ref, before, patch)
    assert rejected["context"]["status"] == "stale_revision", rejected
    current = runtime.query_nodes(ref)["context"]["revision"]
    assert runtime.patch_nodes(ref, current, patch)["success"]


def test_readonly_type_discriminator_does_not_export_entire_node_catalog(host):
    _, owner, ref = host
    node = owner.node_tree.nodes[0]
    node.bl_rna.properties = [
        NS(
            identifier="type",
            type="ENUM",
            is_readonly=True,
            array_length=0,
            enum_items=[
                NS(identifier=f"NODE_{index}", name="Node", description="large global node catalog entry " * 50)
                for index in range(300)
            ],
        )
    ]
    result = runtime.node_schema(ref, "A", limit=100)
    assert result["success"], result
    record = next(item for item in result["context"]["items"] if item["kind"] == "PROPERTY")
    assert record["readonly"] and record["value"] == "VALUE"
    assert "enum_items" not in record


def test_schema_excludes_class_icon_catalog_but_preserves_node_enum(host):
    _, owner, ref = host
    node = owner.node_tree.nodes[0]
    node.bl_icon = "NONE"
    node.data_type = "FLOAT"
    node.bl_rna.properties = [
        NS(
            identifier="bl_icon",
            type="ENUM",
            is_readonly=False,
            array_length=0,
            enum_items=[
                NS(identifier=f"ICON_{index}", name="Icon", description="global icon " * 100) for index in range(1033)
            ],
        ),
        NS(
            identifier="data_type",
            type="ENUM",
            is_readonly=False,
            array_length=0,
            enum_items=[NS(identifier="FLOAT", name="Float", description="Node float mode")],
        ),
    ]
    result = runtime.node_schema(ref, "A", limit=100)
    assert result["success"], result
    records = {item["identifier"]: item for item in result["context"]["items"] if item["kind"] == "PROPERTY"}
    assert "bl_icon" not in records
    assert records["data_type"]["enum_items"][0]["identifier"] == "FLOAT"


def test_real_project_helper_accepts_explicit_material_without_shader_info(host):
    from tests.native_node_project import run_project_node_check

    bpy, owner, _ = host
    owner.node_tree.as_pointer = lambda: 101
    prop = NS(
        name="default_value",
        identifier="default_value",
        type="FLOAT",
        is_readonly=False,
        array_length=0,
        hard_min=0.0,
        hard_max=1.0,
    )
    for node in owner.node_tree.nodes:
        node.inputs[0].bl_rna.properties = Collection([prop])
    bpy.context = NS(scene=NS(objects=[NS(material_slots=[NS(material=owner)])]))
    result = run_project_node_check(material_name="Face")
    assert result["passed"] and result["npr_status"] == "absent"
    assert result["npr_type"] is None and result["npr_schema_page"] is None
    assert result["same_material_query_and_patch"]
    assert result["readback"]["requested"] == pytest.approx(0.51)
    assert owner.node_tree.nodes[0].inputs[0].default_value == 0.5
    assert owner.node_tree.nodes[0].label == "human label"


def test_project_helper_reports_distinct_complex_and_patch_materials(host):
    from tests.native_node_project import run_project_node_check

    bpy, face, _ = host
    layer = face.copy()
    layer.name = "Layer"
    face.node_tree.as_pointer = lambda: 101
    layer.node_tree.as_pointer = lambda: 102
    face.node_tree.nodes[0].inputs[0].default_value = 0.75
    prop = NS(
        name="default_value",
        identifier="default_value",
        type="FLOAT",
        is_readonly=False,
        array_length=0,
        hard_min=0.0,
        hard_max=1.0,
    )
    for node in layer.node_tree.nodes:
        node.inputs[0].bl_rna.properties = Collection([prop])
    bpy.context = NS(scene=NS(objects=[NS(material_slots=[NS(material=face), NS(material=layer)])]))
    result = run_project_node_check(material_name="Face", patch_material_name="Layer")
    assert result["source_material"] == "Face"
    assert result["patched_source_material"] == "Layer"
    assert not result["same_material_query_and_patch"]
    assert result["readback"]["previous"] == 0.5
    assert face.node_tree.nodes[0].inputs[0].default_value == 0.75
    assert layer.node_tree.nodes[0].inputs[0].default_value == 0.5
