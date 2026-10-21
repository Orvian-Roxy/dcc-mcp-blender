"""Return Blender version, scene path, and basic session statistics."""

from __future__ import annotations

import os
import sys

from dcc_mcp_core.skill import skill_entry, skill_error, skill_exception, skill_success


def get_session_info() -> dict:
    """Return Blender version, scene path, and basic stats.

    Returns:
        ActionResultModel dict with version, scene, fps information.
    """
    try:
        import bpy

        from dcc_mcp_blender._scene_files import file_state

        scene = bpy.context.scene
        jobs, job_errors = {}, {}
        for job_type in ("RENDER", "RENDER_PREVIEW", "OBJECT_BAKE", "COMPOSITE", "SHADER_COMPILATION"):
            try:
                jobs[job_type] = bool(bpy.app.is_job_running(job_type))
            except (TypeError, ValueError, RuntimeError) as exc:
                jobs[job_type] = None
                job_errors[job_type] = f"{type(exc).__name__}: {exc}"
        info = {
            "pid": os.getpid(),
            "blender_version": ".".join(str(v) for v in bpy.app.version),
            "blender_version_string": bpy.app.version_string,
            "python_version": sys.version,
            "scene_name": scene.name,
            "scene_file": bpy.data.filepath or "<unsaved>",
            "scene_modified": bpy.data.is_dirty,
            "file_state": file_state(),
            "fps": scene.render.fps,
            "frame_current": scene.frame_current,
            "frame_start": scene.frame_start,
            "frame_end": scene.frame_end,
            "object_count": len(bpy.data.objects),
            "mesh_count": len(bpy.data.meshes),
            "material_count": len(bpy.data.materials),
            "jobs": jobs,
            "job_errors": job_errors,
            "animation_playing": any(
                window.screen.is_animation_playing
                for window in bpy.context.window_manager.windows
                if window.screen is not None
            ),
        }
        return skill_success(
            "Blender session info",
            **info,
            prompt="Check the result with list_objects or use related actions to continue.",
        )
    except ImportError:
        return skill_error("Blender not available", "bpy could not be imported")
    except Exception as exc:
        return skill_exception(exc, message="Failed to get session info")


@skill_entry
def main(**kwargs) -> dict:
    """Entry point; delegates to :func:`get_session_info`."""
    return get_session_info(**kwargs)


if __name__ == "__main__":
    from dcc_mcp_core.skill import run_main

    run_main(main)
