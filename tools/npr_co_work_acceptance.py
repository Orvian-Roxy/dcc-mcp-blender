"""Exercise an already running, test-owned NPR through the official DCC CLI.

Run: python tools/npr_co_work_acceptance.py --config /absolute/config.json
Required JSON keys: cli_path, base_url, instance_id, work_dir, source_root.
The runner never starts or closes Blender. All created files stay in work_dir.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
import uuid
from pathlib import Path


def canonical(value):
    """Keep the structured representation instead of printing its text duplicate."""
    if isinstance(value, dict):
        for key in ("structuredContent", "structured_content", "output", "result"):
            if isinstance(value.get(key), dict):
                return canonical(value[key])
        content = value.get("content", [])
        if len(content) == 1 and content[0].get("type") == "text":
            try:
                return canonical(json.loads(content[0]["text"]))
            except (ValueError, KeyError):
                pass
    return value


class Cli:
    def __init__(self, config, output):
        self.config = config
        self.output = output
        self.calls = []

    def invoke(self, arguments, transport="rest", client="agent-one"):
        command = [
            self.config["cli_path"],
            "--base-url",
            self.config["base_url"],
            "--no-auto-gateway",
            "--non-interactive",
            "--output",
            "json",
            "--transport",
            transport,
            "--agent-session-id",
            client,
            *arguments,
        ]
        started = time.perf_counter()
        completed = subprocess.run(command, capture_output=True, text=True, timeout=100, check=False)
        elapsed = time.perf_counter() - started
        try:
            value = canonical(json.loads(completed.stdout))
        except json.JSONDecodeError:
            value = {"raw_stdout": completed.stdout, "stderr": completed.stderr}
        record = dict(
            number=len(self.calls) + 1,
            transport=transport,
            client=client,
            command=arguments,
            cli_wall_seconds=elapsed,
            exit_code=completed.returncode,
            result=value,
        )
        self.calls.append(record)
        self.output.write_text(json.dumps(self.calls, ensure_ascii=False, indent=2))
        if completed.returncode and value.get("success") is not False:
            raise RuntimeError(f"CLI call {record['number']} failed: {json.dumps(value)[:1200]}")
        return value

    def call(self, tool, arguments=None, transport="rest", client="agent-one"):
        selectors = ["--dcc-type", "blender", "--instance-id", self.config["instance_id"]]
        if transport == "mcp":
            tool = "blender." + self.config["instance_id"].replace("-", "")[:8] + "." + tool
            # Selectors force CLI direct-backend routing, which rejects MCP.
            # The exact discovered instance prefix in the gateway slug pins this route.
            arguments = {"tool_slug": tool, "arguments": arguments or {}}
            tool = "call"
            selectors = []
        return self.invoke(
            ["call", tool, *selectors, "--json", json.dumps(arguments or {})],
            transport,
            client,
        )

    def python(self, code, transport="rest", client="agent-one"):
        value = self.call("blender_scripting__execute_python", {"code": code}, transport, client)
        if value.get("success") is not True:
            raise RuntimeError(f"Python tool failed: {json.dumps(value)[:1200]}")
        return json.loads(value["context"]["stdout"].strip().splitlines()[-1])

    def confirm_saved(self, result):
        if result["context"]["save_status"] == "saved":
            return result
        assert result["context"]["save_status"] == "pending", result
        for _ in range(5):
            observed = self.call("blender_scene__get_session_info")["context"]["file_state"]
            if observed["save_status"] == "saved":
                assert observed["filepath"] == result["context"]["filepath"], observed
                return observed
        raise RuntimeError("Save remained pending after five passive observations; no write was replayed")


def run(config):
    for key in ("cli_path", "base_url", "instance_id", "work_dir", "source_root"):
        if not config.get(key):
            raise ValueError(f"Missing config key: {key}")
    work = Path(config["work_dir"]).expanduser().resolve()
    run_dir = work / ("co-work-" + uuid.uuid4().hex[:10])
    run_dir.mkdir(parents=True)
    cli = Cli(config, run_dir / "calls.json")
    report = {"config": config, "run_dir": str(run_dir), "checks": [], "passed": False}
    try:
        for skill in ("blender-scene", "blender-scripting"):
            cli.invoke(["load-skill", skill, "--dcc-type", "blender", "--instance-id", config["instance_id"]])
        initial = cli.python("""import json, os, threading
import dcc_mcp_blender, dcc_mcp_core
import dcc_mcp_blender._scene_files as scene_files
from dcc_mcp_blender._scene_files import file_state
views = lambda: [(a.type, a.spaces.active.shading.type, a.spaces.active.region_3d.view_perspective, a.spaces.active.region_3d.view_distance, list(a.spaces.active.region_3d.view_location), list(a.spaces.active.region_3d.view_rotation)) for w in bpy.context.window_manager.windows for a in w.screen.areas if a.type == 'VIEW_3D']
assert threading.current_thread() is threading.main_thread()
assert not bpy.data.is_dirty, 'Use a fresh test-owned instance with no unsaved edits'
print(json.dumps(dict(pid=os.getpid(), filepath=bpy.data.filepath, views=views(), background=bpy.app.background, blender=bpy.app.version_string, build=bpy.app.build_hash.decode(), adapter=dcc_mcp_blender.__version__, core=dcc_mcp_core.__version__, adapter_source=scene_files.__file__, initial_objects=list(bpy.data.objects.keys()))))""")
        assert Path(initial["adapter_source"]).resolve().is_relative_to(Path(config["source_root"]).resolve()), initial
        report["identity"] = initial
        name = "CoWork_" + run_dir.name
        human = name + "_Human"
        target = str(run_dir / "scene.blend")
        cli.python(f"""import json
obj = bpy.data.objects.new({name!r}, None)
bpy.context.scene.collection.objects.link(obj)
obj['agent'] = 1
print(json.dumps(dict(created=obj.name)))""")
        saved = cli.call("blender_scene__save_scene", {"filepath": target})
        cli.confirm_saved(saved)
        report["checks"].append("REST typed save confirmed")
        edited = cli.python(
            f"""import json
bpy.ops.object.empty_add()
obj = bpy.context.object
obj.name = {human!r}
obj['human'] = 27
bpy.data.objects[{name!r}]['human_edit'] = 42
print(json.dumps(dict(human=obj.name)))""",
            "mcp",
            "independent-human-simulation",
        )
        assert edited["human"] == human
        edited = cli.python(
            f"""import json
obj = bpy.data.objects[{name!r}]
assert obj['human_edit'] == 42
assert bpy.data.objects[{human!r}]['human'] == 27
obj['agent'] = 2
print(json.dumps(dict(human_edit=obj['human_edit'], agent=obj['agent'])))""",
            "mcp",
            "agent-two",
        )
        assert edited == {"human_edit": 42, "agent": 2}
        report["checks"].append("MCP second client preserves independently simulated human edits")
        refused = cli.call("blender_scene__open_scene", {"filepath": target})
        assert refused.get("error") == "unsaved_changes", refused
        assert refused["context"].get("pending_managed_write") is True, refused
        report["managed_write_guard"] = refused["context"]
        refused = cli.call("blender_scene__new_scene")
        assert refused.get("error") == "unsaved_changes", refused
        script = cli.python(f"""import json, threading, time
from dcc_mcp_blender._scene_files import open_scene, new_scene, close_scene, save_scene
assert threading.current_thread() is threading.main_thread()
started = time.perf_counter()
results = [open_scene({target!r}), new_scene(), close_scene()]
assert all(item.get('error') == 'unsaved_changes' for item in results)
saved = save_scene({target!r})
assert saved['context']['save_status'] in ('saved', 'pending'), saved
print(json.dumps(dict(guards=[item['error'] for item in results], save_status=saved['context']['save_status'], blender_operation_seconds=time.perf_counter()-started)))""")
        image_check = cli.python(f"""import json
from dcc_mcp_blender._scene_files import new_scene, save_scene
image = bpy.data.images.new({(name + "_Paint")!r}, width=1, height=1)
image.pixels[0] = 0.5
assert image.is_dirty
saved = save_scene({target!r})
assert saved['context']['save_status'] in ('saved', 'pending'), saved
refused = new_scene()
assert refused.get('error') == 'unsaved_changes', refused
assert image.name in refused['context']['dirty_images'], refused
image.filepath_raw = {str(run_dir / "paint.png")!r}
image.file_format = 'PNG'
image.save()
assert not image.is_dirty
saved = save_scene({target!r})
assert saved['context']['save_status'] in ('saved', 'pending'), saved
print(json.dumps(dict(protected_image=image.name, image_saved=True)))""")
        report["image_guard"] = image_check
        pending = cli.call("blender_scene__get_session_info")["context"]["file_state"]
        assert pending["save_status"] != "saved" and pending["pending_managed_write"], pending
        cli.confirm_saved(cli.call("blender_scene__save_scene", {"filepath": target}))
        observed = cli.call("blender_scene__get_session_info")["context"]["file_state"]
        assert observed["save_status"] == "saved", observed
        report["script_batch"] = script
        report["checks"].append("typed and script-batch guards; main-thread direct shared save")
        # The official CLI supports gateway batch only over REST, never MCP.
        prefix = "blender." + config["instance_id"].replace("-", "")[:8] + "."
        batch = cli.invoke(
            [
                "call",
                "--batch",
                "--steps",
                json.dumps(
                    [
                        {"tool_slug": prefix + "blender_scene__get_session_info", "arguments": {}},
                        {"tool_slug": prefix + "blender_scene__save_scene", "arguments": {"filepath": target}},
                    ]
                ),
            ]
        )
        assert batch.get("success") is True, batch
        assert len(batch["results"]) == 2 and all(item.get("ok") is True for item in batch["results"]), batch
        report["rest_batch"] = batch
        final = cli.python(
            f"""import json, os
from dcc_mcp_blender._scene_files import file_state, save_scene
assert bpy.data.objects[{name!r}]['agent'] == 2
assert bpy.data.objects[{name!r}]['human_edit'] == 42
assert bpy.data.objects[{human!r}]['human'] == 27
views = [(a.type, a.spaces.active.shading.type, a.spaces.active.region_3d.view_perspective, a.spaces.active.region_3d.view_distance, list(a.spaces.active.region_3d.view_location), list(a.spaces.active.region_3d.view_rotation)) for w in bpy.context.window_manager.windows for a in w.screen.areas if a.type == 'VIEW_3D']
saved = save_scene({target!r})
assert saved['context']['save_status'] in ('saved', 'pending'), saved
print(json.dumps(dict(pid=os.getpid(), views=views, file_state=file_state(), objects=list(bpy.data.objects.keys()))))""",
            "mcp",
            "agent-two",
        )
        assert set(initial["initial_objects"]).issubset(final["objects"]), final
        cli.confirm_saved(cli.call("blender_scene__save_scene", {"filepath": target}))
        final["file_state"] = cli.call("blender_scene__get_session_info")["context"]["file_state"]
        assert final["pid"] == initial["pid"]
        assert final["views"] == initial["views"]
        assert final["file_state"]["save_status"] == "saved", final
        report["final"] = final
        report["checks"].append("REST gateway batch; final MCP read; same live process and unchanged shading")
        report["passed"] = True
    except Exception as exc:
        report["error"] = str(exc)
        raise
    finally:
        report["cli_calls"] = len(cli.calls)
        report["cli_wall_seconds"] = sum(item["cli_wall_seconds"] for item in cli.calls)
        report["timing_note"] = (
            "CLI wall time includes transport and Blender work; script_batch separately measures its Blender operations. Cold start is owned by the launcher."
        )
        (run_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(
            json.dumps(
                {"passed": report["passed"], "report": str(run_dir / "report.json"), "cli_calls": len(cli.calls)}
            )
        )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args()
    run(json.loads(args.config.read_text()))


if __name__ == "__main__":
    main()
