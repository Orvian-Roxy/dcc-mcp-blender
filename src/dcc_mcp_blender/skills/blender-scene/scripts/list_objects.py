"""Return a bounded page from Blender object datablocks."""

from __future__ import annotations

from typing import Optional

from dcc_mcp_core.skill import skill_entry, skill_error, skill_exception, skill_success


def list_objects(object_type: Optional[str] = None, name_filter: str = "", offset: int = 0, limit: int = 20) -> dict:
    """List a filtered page from bpy.data.objects in its existing order.

    Args:
        object_type: Optional Blender object type filter, e.g. ``"MESH"``,
            ``"LIGHT"``, ``"CAMERA"``, ``"ARMATURE"``.

        name_filter: Case-sensitive literal name substring, not a regex.
        offset: Nonnegative offset among matching objects.
        limit: Page size from 1 to 100 (default 20).

    Returns:
        ActionResultModel with objects, count/total matching objects, returned
        page size and next_offset (null when exhausted).
    """
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
        return skill_error("Use offset >= 0 and limit between 1 and 100 (integers)", "invalid_pagination")
    if not isinstance(name_filter, str):
        return skill_error("name_filter must be a literal string", "invalid_name_filter")
    try:
        import bpy

        objects = [
            obj
            for obj in bpy.data.objects
            if (not object_type or obj.type == object_type.upper()) and name_filter in obj.name
        ]
        total = len(objects)
        page = objects[offset : offset + limit]
        result = [
            {
                "name": o.name,
                "type": o.type,
                "location": list(o.location),
                "visible": not o.hide_viewport,
            }
            for o in page
        ]
        return skill_success(
            f"Returned {len(result)} of {total} matching objects",
            objects=result,
            count=total,
            total=total,
            returned=len(result),
            next_offset=offset + len(result) if offset + len(result) < total else None,
            prompt="Follow next_offset with the same filters to enumerate more objects; null means this query is exhausted.",
        )
    except ImportError:
        return skill_error("Blender not available", "bpy could not be imported")
    except Exception as exc:
        return skill_exception(exc, message="Failed to list objects")


@skill_entry
def main(**kwargs) -> dict:
    """Entry point; delegates to :func:`list_objects`."""
    return list_objects(**kwargs)


if __name__ == "__main__":
    from dcc_mcp_core.skill import run_main

    run_main(main)
