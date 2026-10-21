---
name: blender-nodebpy-runtime
description: "Prepare NodeBPY dependencies, build supported game candidates and verify current Blender edits."
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

Audited `wuwa` / `WutheringWaves` and `hsr` / `HonkaiStarRail` verification only
observes the current candidate, preserving managed revision and any existing
pending edits even when verification fails. Genshin reconstruction verification
also observes bindings and independently decodes external images when the loaded
project `execute` declares that capability. Older Genshin checkouts and ZZZ
verification retain conservative managed-write classification. Independent decode
checks the file without reloading scene image pixels; build/packaging reloads keep
their existing purpose.
After a possible write, use the typed save tool and read-only session state to
confirm persistence. Arbitrary Python remains a possible write, including queries.

`context.project_elapsed_seconds` measures the project entry call, including its
import, validation, computation and file operations. CLI wall time minus this value
is approximate surrounding overhead, not a separate measurement of GPU or CPU time.

Supported games are `genshin`, `wuwa`, `zzz` and `hsr` (or their project directory names). HSR opens its Profile's character-free template only for a fresh co-work build. Its host input check reports the PMX inventory checks deferred to the registered importer during `run`; keep those limits visible when reporting readiness.
Successful calls return a bounded summary with status, output, save state, elapsed
time, available scale counts and qualification limits. `full=true` returns the
current call's complete details. Errors always retain full evidence.

A successful `run` writes its full receipt beside the candidate as
`<stem>.dcc-build-report.json` and returns `report_path` in either mode. Read this
file for the original import and timing details without repeating the build.
`verify` returns current validation and any existing `build_report_path`; it never
replaces the original receipt. Report creation uses exclusive mode. If it fails,
`report_error` explains the auxiliary failure and the successful build's complete
result is returned, so it must not be retried just to obtain the report.
