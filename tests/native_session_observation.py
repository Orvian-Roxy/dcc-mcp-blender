"""Bounded background-only session/HSR management test using synthetic data.

Run the selected Blender with --background --factory-startup --disable-autoexec
--python-exit-code 1 --python tests/native_session_observation.py --
--site-packages <existing core dependencies> --output-dir <new temporary folder>
--expected-binary <selected Blender executable>. Isolate BLENDER_USER_* paths.
HSR business validation is an explicit fixture callback, not a character check.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site-packages", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--expected-binary", required=True, type=Path)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])
    source = Path(__file__).resolve().parents[1]
    sys.path[:0] = [str(source / "src"), str(args.site_packages.resolve())]

    import bpy
    import yaml
    from dcc_mcp_core import HostExecutionBridge
    from dcc_mcp_core.skill import skill_success

    from dcc_mcp_blender import _managed_writes as writes
    from dcc_mcp_blender import _nodebpy_runtime_ops as project_ops
    from dcc_mcp_blender._scene_files import file_state, guard_before_replacement

    assert bpy.app.background, "This fixture must never run in a GUI instance"
    assert Path(bpy.app.binary_path).resolve() == args.expected_binary.resolve()
    assert not bpy.data.filepath, "This fixture requires factory startup without an input file"
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    skills = source / "src/dcc_mcp_blender/skills"
    scene_tools = yaml.safe_load((skills / "blender-scene/tools.yaml").read_text())["tools"]
    bridge = HostExecutionBridge()
    writes.install_write_guard(
        bridge,
        lambda name: (
            SimpleNamespace(tools=[SimpleNamespace(**tool) for tool in scene_tools])
            if name == "blender-scene"
            else None
        ),
    )

    def call(skill, tool, **params):
        return bridge.execute_script(
            str(skills / skill / "scripts" / (tool + ".py")),
            params,
            action_name=skill.replace("-", "_") + "__" + tool,
            skill_name=skill,
        )

    image = bpy.data.images.new("DCC synthetic pixels", width=2, height=2)
    image.filepath_raw = str(output / "synthetic.png")
    image.file_format = "PNG"
    image.pixels[:] = [1.0, 0.0, 0.0, 1.0] * 4
    image.save()
    assert not image.is_dirty
    candidate = output / "synthetic.blend"

    def save():
        result = call("blender-scene", "save_scene", filepath=str(candidate))
        assert result["success"] and result["context"]["save_status"] == "saved", result

    def snapshot():
        return {
            "revision": writes.current_revision(),
            "file_state": file_state(),
            "pixels": list(image.pixels[:4]),
        }

    # The public project entry and save observation remain real; only the
    # asset-dependent business callback and checkout gate are fixture substitutes.
    def fixture_project_call(module, function, params):
        assert (module, function) == ("nodebpy_build", "execute")
        assert params["action"] == "verify" and params["co_work"] is True
        assert params["game"] in {"hsr", "HonkaiStarRail"}
        return skill_success(
            "Synthetic HSR management fixture; no character validation",
            project_elapsed_seconds=0.0,
            result={
                "ok": True,
                "status": "current_saved_scene_verified",
                "fixture_callback": True,
                "details": {
                    "current_scene_saved": not bpy.data.is_dirty and not image.is_dirty,
                    "scene": {"fixture_callback": True},
                },
            },
        )

    save()
    report = {
        "scope": "synthetic_native_session_and_hsr_management_boundary",
        "hsr_business_validation": "fixture_callback_only; no real character/assets read",
        "pid": os.getpid(),
        "binary_path": bpy.app.binary_path,
        "version": bpy.app.version_string,
        "build_hash": bpy.app.build_hash.decode(),
        "background": bpy.app.background,
        "cases": [],
    }
    with patch.object(project_ops, "_check_checkout"), patch.object(
        project_ops, "_project_call", side_effect=fixture_project_call
    ):
        for case in ("clean", "prior_pending", "dirty_image"):
            if case == "prior_pending":
                query = call("blender-scripting", "execute_python", code="result = bpy.app.is_job_running('RENDER')")
                assert query["success"] and writes.pending_write(), query
                assert not bpy.data.is_dirty and not image.is_dirty
            elif case == "dirty_image":
                save()
                image.pixels[0] = 0.25
                image.update()
                assert image.is_dirty and not writes.pending_write()
            before = snapshot()
            observed = call("blender-scene", "get_session_info")
            assert observed["success"], observed
            info = observed["context"]
            assert info["pid"] == os.getpid()
            assert info["job_errors"] == {}, info
            assert info["jobs"] == {kind: bpy.app.is_job_running(kind) for kind in info["jobs"]}
            assert info["animation_playing"] is False
            assert snapshot() == before, "Typed session inspection changed native/managed state"
            verifications = []
            for game in ("hsr", "HonkaiStarRail"):
                result = call(
                    "blender-nodebpy-runtime",
                    "project",
                    project_root=str(source),
                    action="verify",
                    game=game,
                    profile="synthetic-fixture-only",
                    output=str(candidate),
                    full=True,
                )
                assert result["success"], result
                assert result["context"]["result"]["fixture_callback"] is True
                assert snapshot() == before, "HSR management boundary changed native/managed state"
                saved = result["context"]["result"]["details"]["current_scene_saved"]
                assert saved is (case == "clean"), result
                verifications.append({"game": game, "current_scene_saved": saved})
            refusal = guard_before_replacement()
            assert (refusal is None) is (case == "clean"), refusal
            report["cases"].append(
                dict(
                    case=case,
                    state=before,
                    jobs=info["jobs"],
                    animation_playing=info["animation_playing"],
                    hsr_fixture_verifications=verifications,
                    replacement_error=refusal.get("error") if refusal else None,
                    state_preserved=True,
                )
            )
    bridge.clear_script_packages()
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print("DCC_NATIVE_SESSION_RESULT " + json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
