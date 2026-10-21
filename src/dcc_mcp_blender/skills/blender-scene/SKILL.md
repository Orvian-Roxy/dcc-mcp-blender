---
name: blender-scene
description: "Blender scene management — create, open, save, list and inspect scene objects"
license: "MIT"
allowed-tools: ["Bash", "Read"]
metadata:
  dcc-mcp:
    dcc: blender
    layer: domain
    stage: bootstrap
    version: "1.0.0"
    tags: [blender, scene, hierarchy]
    search-hint: "new scene, open, save, list objects, hierarchy, scene info, session, PID, native jobs, playback, dirty images, viewport state, shading"
    search-aliases: [scene summary, session info, get scene, new blend file, open blend, save blend, inspect scene, list objects]
    intent: "Inspect and manage Blender scene lifecycle — create, open, save scenes, list objects, and retrieve session diagnostics."
    recall-context:
      app_type: blender
      domain: scene
      workflow_stage: bootstrap
      task_category: query
    preconditions:
      - type: software
        name: blender
        version: ">=4.0"
    side-effects:
      creates: true
      modifies: true
      file_output: true
      targets: [scene, file:blend]
    produces: [scene_info, session_info, file:blend, object_list]
    requires: []
    tools: tools.yaml
---

# blender-scene

Blender scene management skill — create, open, save and inspect scenes.

Use `list_objects` for a bounded object query: default `limit=20`, maximum 100. Filter with the existing case-insensitive `object_type` and/or case-sensitive literal `name_filter` before paging. The scope remains `bpy.data.objects`, including objects outside the current scene. `count` and `total` are the full matched count; `returned` is this page size. For exhaustive enumeration, repeat the same filters with `offset=next_offset` until it is null. Unlike the earlier unbounded default, `{}` returns only the first page; scene changes between calls can change page membership.

Use `get_viewport_info` with no arguments to observe existing GUI viewports (shading and view transforms). It does not change context, evaluate geometry, or capture pixels; headless sessions return an empty list. Use material-library `list_images` and `image_file_status` for image observations.

Use `get_session_info` with no arguments for the exact host `pid`, scene save state and `file_state.dirty_images`, native `jobs`, and `animation_playing` across the current process's windows. Jobs cover `RENDER`, `RENDER_PREVIEW`, `OBJECT_BAKE`, `COMPOSITE` and `SHADER_COMPILATION`; an unsupported query returns null with its reason in `job_errors`, never a false idle claim. These observations preserve existing dirty/pending edits. They do not report external Fleet jobs or prove a process is safe to close.

Use `set_viewport_view` with the observed `window_index` and `area_index` to navigate that viewport. Supply an `axis` or wxyz `view_rotation`, and optionally `view_location`, positive `view_distance` or `view_perspective` (`ORTHO`/`PERSP`). Optional `shading_type`, `use_compositor` and `show_overlays` set viewport display; a display-only call preserves navigation. It changes only viewport UI, preserving object visibility, camera objects and existing scene/image edits. Leave camera view with an explicit axis or perspective before navigating. Navigation/display do not add or clear managed scene writes; use a normal save if the user wants the new view persisted in the file.

A clean typed `open_scene` reports saved file state with `state_source: open` after confirming stable file metadata. This is an open observation, not a new save: `last_save` remains empty. File changes, scene/image edits or later managed writes invalidate it. A later failed save does not fall back to the old open observation.

After `save_unconfirmed`, observe `get_session_info` before any open/close attempt. Continue only after the file state confirms the save and no scene/image edits or managed writes remain. A rejected open is still a potential managed write and can invalidate delayed save confirmation.
