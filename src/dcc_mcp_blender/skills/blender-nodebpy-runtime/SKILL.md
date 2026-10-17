---
name: blender-nodebpy-runtime
description: "Prepare NodeBPY dependencies, build a Genshin candidate and verify current Blender edits."
license: "MIT"
metadata:
  dcc-mcp:
    dcc: blender
    layer: domain
    stage: diagnostics
    version: "1.0.0"
    tags: [blender, nodebpy, mmd, character]
    search-hint: "NodeBPY dependency readiness, character build, Genshin current-scene verification"
    tools: tools.yaml
---

# NodeBPY runtime

Use `prepare` with an explicit NodeBPY-Bridges checkout and existing external
NodeBPy dependency paths. It attaches those paths, enables the specified installed
MMD add-on for this process and checks the project's Blender doctor. Missing paths,
registration failures and nested doctor failures are errors. It never installs packages.
For an Extensions install, pass its full `bl_ext.<repository>.<package>` module name.

Use `project` with action `check`, `run` or `verify`, the game, Profile and an
independent output under the project's asset work directory. `run` invokes the
project builder directly and keeps the GUI open. Existing outputs, unsaved scenes
and dirty image pixels prevent initial template replacement. `verify` checks the
current candidate without reopening it or saving human edits; its result separates
structural validity, save state and pending visual acceptance. Save through the
owning DCC file tool when needed. An existing `single_project_v1` connection retains
its own workflow and cannot enter this path.

The adapter checks shared save evidence after `run` and `verify`. Pending managed
Python edits or an unknown save observation downgrade the project's saved claim,
while preserving structural verification. Observation does not save or reopen.

Existing reconstruction validators may temporarily switch and restore material
bindings. `verify` therefore remains a conservative managed write. To establish
a saved scene afterward, use the typed save tool and read-only session state.

`context.project_elapsed_seconds` measures the project entry call, including its
import, validation, computation and file operations. CLI wall time minus this value
is approximate surrounding overhead, not a separate measurement of GPU or CPU time.
