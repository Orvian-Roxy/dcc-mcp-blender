---
name: blender-node-runtime
description: "Bounded live node queries, actual RNA/socket schema, and revision-checked local Shader/Compositor patches."
license: "MIT"
allowed-tools: ["Bash", "Read"]
metadata:
  dcc-mcp:
    dcc: blender
    layer: domain
    stage: lookdev
    version: "1.0.0"
    tags: [blender, nodes, shader, compositor, revision, schema]
    search-hint: "local node query, dynamic socket schema, stale revision, safe node patch"
    intent: "Inspect a bounded part of a live graph and apply verified local changes."
    tools: tools.yaml
---

# Local node editing

Use `query_targets` to choose an owner. Every graph call takes a `tree_ref` with
`tree_type` and `owner: {kind, name}`. Supported types are `ShaderNodeTree`,
`CompositorNodeTree` and `GeometryNodeTree`; owners are `MATERIAL`, `WORLD`,
`LIGHT`, `SCENE` or `NODE_GROUP`, where appropriate.

Use `query_nodes` for a page and its incident links. Continue with `next_offset`
and `next_link_offset`. Use `node_schema` for the actual existing node's current
socket identifiers, editable RNA enums and ranges. Readonly fields report their
actual value; class registration fields such as `bl_icon` are excluded. Schema
inspection creates no probe.
Python add-on nodes remain inspectable; their mutation is not qualified.

Pass the returned `revision` as `base_revision` to `patch_nodes`. Supported
operations are `set_input`, `set_output`, `set_label`, `set_location`, `connect`
and `disconnect`. Values use `node`, `socket` (the exact identifier) and `value`.
Links use `from_node`, `from_socket`, `to_node` and `to_socket`. Disconnect an
existing input explicitly before replacing its connection. Multi-input ordering
and node creation/deletion use existing DCC tools, outside this transaction.

`action: validate` tests on an owned disposable copy. `apply` validates again,
rejects stale revisions, commits and compares readback. Failure reports
`rolled_back` or `rollback_incomplete`; observe the graph before any retry.
Copies may mark Blender dirty even after cleanup; the result reports both flags
and cleanup failures. The implementation never resets the user's dirty flag.

A shared tree requires `allow_shared: true`, which affects all its users.
Material-local node values never change referenced groups. Revisions include referenced group content up to eight levels and actual
ID identity, ColorRamp, CurveMapping and image/texture mapping settings. Target a
group explicitly to edit it. Unsupported node storage refuses patches while
query/schema remain available. Geometry is query/schema only here; use existing `blender-geometry-nodes`
v1 mutation tools. No generic Geometry transaction is claimed.

Defaults are 20 nodes/links or 40 schema items. Pages are limited to 100 entries
and 64 KiB. Revision inspection rejects graphs over eight group levels or an aggregate
10,000 nodes / 50,000 links, ramps over 256 elements and curves over 256 points.
A failed response asks for a smaller page; it does not silently omit data.

The RNA property schema is selectively adapted from newo; its source and MIT
notice are in [THIRD_PARTY_LICENSE.txt](THIRD_PARTY_LICENSE.txt).
