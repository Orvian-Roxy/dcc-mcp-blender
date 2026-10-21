---
name: blender-node-runtime
description: "Bounded live node queries, actual RNA/socket schema, revision-checked patches and batched parameter/material previews."
license: "MIT"
allowed-tools: ["Bash", "Read"]
metadata:
  dcc-mcp:
    dcc: blender
    layer: domain
    stage: lookdev
    version: "1.0.0"
    tags: [blender, nodes, shader, compositor, revision, schema, preview]
    search-hint: "local node query, dynamic socket schema, stale revision, safe node patch, batch parameter preview, material slot isolation"
    intent: "Inspect a bounded part of a live graph and apply verified local changes."
    tools: tools.yaml
---

# Local node editing

Use `query_targets` when the owner is unknown; use a known owner directly. Every graph call takes a `tree_ref` with
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

Closure input/output item revisions include native item order, name, socket type
and structure type, up to 256 items; socket identifiers are recorded separately.
Closure pairing records the output node's name and type within the tree; its
contents participate in the same tree revision without recursive pairing reads.

NPR menu items and the active menu item are included in revisions. Empty GLSL
`define_values` are supported; nonempty define collections remain unsupported
for strict patches and previews.

For several parameter candidates, use `preview_variants` once. Supply `targets`
with a local `id`, `tree_ref`, `base_revision` and optional `allow_shared`; each
variant supplies its `id` and `patches: [{target, operations}]`. Node operations support `set_input`, `set_output`, `connect` and `disconnect`.
Output isolation uses existing nodes and exact sockets; replacing an occupied
input requires `disconnect` followed by `connect`. Muted links and connections that would
exceed a source socket link limit are rejected. Node creation/deletion is not supported. Linked input defaults are rejected; change the
parameter driving that connection. A batch accepts up to 16 targets and 16
variants, with at most 100 operations per variant.

A property target instead uses `{id, owner: {type: "object", name}, path,
base_value}` and operations `[{op: "set_property", value}]`. Read `base_value`
through `blender-rna-runtime/get_properties`. Supported paths are native light
`data.energy`/`data.color`, object `hide_render`/`hide_viewport`, and an existing
Displace modifier's `modifiers["name"].strength`. Other RNA fields and plugin
callbacks and shared Light datablocks are excluded. Visibility here changes the named RNA flag, not
`hide_set`; keep the existing view-layer hide state.

To isolate one material layer within a mesh, supply a material target:

```json
{"id":"face5","owner":{"type":"object","name":"Character"},"material_slot":5,"base_link":"DATA","base_material":"Face Overlay"}
```

Read the zero-based slot's current `link` and effective `material` using the RNA
query tools; `base_material` is its exact name, or `null` for an empty slot.
Use exactly one operation per slot patch:
`{"target":"face5","operations":[{"op":"set_material","material":"Diagnostic Transparent"}]}`.
The replacement must already exist; choose a known transparent or unlit solid
material for the intended control. This tool neither creates nor edits material
datablocks, including shared or linked materials. A transparent replacement tests
that material's actual render path; it does not remove geometry or guarantee that
its depth/shadow contribution disappears.

Only local editable mesh objects in Object mode and writable slot bindings are
accepted. Stale `base_link`/`base_material`, duplicate slot targets and missing
replacement materials reject the entire batch. During each candidate the slot
uses an OBJECT override, so other users of a shared mesh keep their materials.
Restoration reinstates the original DATA/OBJECT link, effective material and any
dormant object override behind a DATA slot; mesh material entries stay untouched.
No arbitrary RNA pointer assignment is exposed. Node, property and material slot
targets may be mixed. Only the specified values, links and bindings are restored;
there is no whole-scene snapshot.

Each variant starts from the same baseline, captures the current VIEW_3D at the
requested dimensions, and restores its changes before the next candidate.
Include `{"id": "baseline", "patches": []}` as one variant to capture the original
baseline without parameter or link writes. Targets are still required, and the
entire batch is validated before the first capture.
Use an absolute `output_directory` with no existing candidate PNGs or
`preview-manifest.json`. The result contains image paths, byte sizes, timings,
exact candidate patches, restored target revisions/bindings and a JSON manifest; images
are not uploaded automatically. The current view stays fixed, and no `.blend`
is saved. Capture and node application run serially on Blender's main thread.

Failure stops the batch and reports completed images, failure stage and actual
restoration evidence. If `restored` is false, inspect the affected targets before
continuing. Confirmed restoration preserves the prior managed pending state;
failed or unknown restoration is marked as pending. Previewing never clears
Blender's dirty flag or existing managed writes. Apply selected node patches explicitly with `patch_nodes` and the returned target
revisions. Apply selected RNA values with `blender-rna-runtime/set_property`
using their owner and path, then use the project's normal validation and persistence workflow.
Material overrides are diagnostic only; persist an intended binding through the
owning project's source configuration and normal build/validation workflow.

Defaults are 20 nodes/links or 40 schema items. Pages are limited to 100 entries
and 64 KiB. Revision inspection rejects graphs over eight group levels or an aggregate
10,000 nodes / 50,000 links, ramps over 256 elements and curves over 256 points.
A failed response asks for a smaller page; it does not silently omit data.

The RNA property schema is selectively adapted from newo; its source and MIT
notice are in [THIRD_PARTY_LICENSE.txt](THIRD_PARTY_LICENSE.txt).
