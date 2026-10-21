---
name: blender-rna-runtime
description: "Discover live plugin Operators and inspect or edit RNA owner properties when no domain tool fits."
license: "MIT"
allowed-tools: ["Bash", "Read"]
metadata:
  dcc-mcp:
    dcc: blender
    layer: domain
    stage: diagnostics
    version: "1.0.0"
    tags: [blender, rna, operator, plugin, properties]
    search-hint: "live Operator schema and call, plugin PropertyGroup settings, Wiggle Quality"
    search-aliases: [operator poll, RNA property readback, plugin settings]
    intent: "Access registered plugin operations and settings without writing reflection scripts."
    recall-context:
      app_type: blender
      domain: diagnostics
      workflow_stage: diagnostics
      task_category: query
    preconditions:
      - type: software
        name: blender
        version: ">=4.5"
    side-effects:
      modifies: true
      imports: false
      targets: [operator_state, rna_properties]
    produces: [operator_schema, property_readback, operator_status]
    requires: []
    tools: tools.yaml
---

# Live plugin operations and RNA settings

Prefer a domain tool when it covers the task. For type metadata without instance
access, use `blender-rna`. These tools inspect the selected Blender instance's
current registration; they do not cache a full plugin catalog.

Search Operators by identifier or label, then describe the exact result before
calling it. `source_module: null` means the registration does not prove who
provided it, including plugins using a native namespace.

For settings, choose an explicit `owner` with `type` set to `scene`, `object`,
`pose_bone`, `material` or `addon_preferences`. Named owners use `name`; a pose
bone also requires `object_name`. An unnamed scene means the current scene.
Use `get_properties` with a query such as `Quality`, then expand the returned
PropertyGroup through a path such as `wiggle`. A single scalar path returns
`value`; a group returns a page of metadata and available values.

Paths accept public RNA attributes and literal collection keys, for example
`wiggle.iterations` or `modifiers["Subdivision"].levels`. They cannot execute
expressions. `set_property` accepts scalar values and fixed arrays with live
range validation, then returns actual readback. Dynamic getters and setters can
run plugin callbacks. Coupled or selection-sensitive updates, such as changing
two selected Wiggle bones, still use the Native provider.

`call_operator` validates every supplied field against `get_rna_type`; unknown
fields, incorrect types and POINTER/COLLECTION arguments fail before execution.
The optional `context` asserts the current `active_object`, `selected_objects`
and `mode`. It can select an existing `area_type` with a WINDOW region, but does
not create editors, change selection or switch mode. A failed poll reports the
actual context. The execution mode is `EXEC_DEFAULT`.

`operator_status` is the real return value. `CANCELLED` fails, `RUNNING_MODAL`
is incomplete, and neither triggers a retry. `FINISHED` reports completion of
the Operator, with `business_result_verified: false`; inspect the resulting
objects or settings separately.

File replacement, saving, closing and plugin lifecycle operations use the
existing scene/file and plugin typed tools. This boundary does not sandbox
trusted third-party Python or prevent an Operator's indirect side effects.
