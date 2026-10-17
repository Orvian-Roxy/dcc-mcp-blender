---
name: blender-mmd-runtime
description: "Reuse native MMD inspection, Toon profiles, MBTs-NG operations and Wiggle 2 plans, targeted edits and restore receipts"
license: "MIT"
allowed-tools: ["Bash", "Read"]
metadata:
  dcc-mcp:
    dcc: blender
    layer: domain
    stage: authoring
    version: "1.0.0"
    tags: [blender, mmd, toon, mbts, wiggle, native]
    search-hint: "MMD native provider, toon profiles, MBTs-NG, Wiggle 2 exact bone edits, restore transaction"
    intent: "Call the existing native MMD allowlist in the selected Blender instance."
    tools: tools.yaml
---

# Native MMD runtime

Call `list_methods` to find the public native methods and their query, plan,
modify, validate or restore phase. Worker actions are excluded.

Use `blender-dev.attach_project` to attach the existing `blender-mcp-runtime`
checkout and any required dependency paths. This skill imports
`runtime_native.mmd_dispatch` directly in Blender; it does not start or call the
old MCP server. Install and enable each content plugin separately. Missing
plugins affect their own capability report, not discovery of the other methods.
The dependency baseline is Runtime commit
`ce9ae34ac8f0287baf09b39dfe76ffae58d12d68`; no provider source is copied here.

Call `mmd.toon.capabilities`, `mmd.mbts.capabilities` or
`mmd.wiggle.capabilities` with empty parameters to check the corresponding
plugin. A successful probe can still report `data.ready=false`. MMD inspection
uses `scope` and `model_ref`; authoring methods take `parameters.request` using
the provider's existing schema. Inspect the provider report for supported
operations and parameters before mutation.

For example, call `mmd.wiggle.plan` with
`{"request":{"model_ref":"ModelRoot","armature_name":"Rig","bone_names":["Hair"],"endpoint":"tail","parameters":{"stiffness":300}}}`.
Use explicit target names, inspect the plan, apply, validate and retain the
returned transaction identifier for restore. Preserve provider warnings and
conflicts. MBTS has plan/execute only; it does not promise transaction restore.

The call returns the native report once, without a wrapper that turns
`status=error` into success. Do not automatically retry an interrupted write.
Inspect the current target state first. Provider selection handling, target
checks and drift detection remain in the native implementation.
