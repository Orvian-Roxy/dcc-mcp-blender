"""Explicit navigation defaults survive the isolated factory startup."""

import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from tests.test_npr_runtime import launcher


def test_navigation_defaults_are_saved_without_touching_scene():
    inputs = SimpleNamespace(use_mouse_depth_navigate=False, use_zoom_to_mouse=False, use_rotate_around_active=True)
    save = Mock(return_value={"FINISHED"})
    bpy = SimpleNamespace(
        context=SimpleNamespace(preferences=SimpleNamespace(inputs=inputs)),
        ops=SimpleNamespace(wm=SimpleNamespace(save_userpref=save)),
    )
    settings = {"use_mouse_depth_navigate": True, "use_zoom_to_mouse": True, "use_rotate_around_active": False}
    with patch.dict(sys.modules, {"bpy": bpy}):
        assert launcher().prepare_navigation({"navigation_preferences": settings}) == settings
    save.assert_called_once_with()


def test_missing_navigation_defaults_preserves_existing_preferences():
    with patch.dict(sys.modules, {"bpy": None}):
        assert launcher().prepare_navigation({}) == {}


@pytest.mark.parametrize("settings", [{"use_zoom_to_mouse": 1}, {"unrelated_preference": True}, []])
def test_invalid_navigation_defaults_are_rejected_before_mutation(settings):
    with patch.dict(sys.modules, {"bpy": None}), pytest.raises(ValueError, match="navigation_preferences"):
        launcher().prepare_navigation({"navigation_preferences": settings})
