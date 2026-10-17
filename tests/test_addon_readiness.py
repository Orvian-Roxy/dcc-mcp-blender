"""Public add-on lifecycle seams; Blender is replaced at its host boundary."""

import sys
from types import SimpleNamespace

import pytest

from dcc_mcp_blender._dev_ops import disable_addon, enable_addon, get_addon_status, list_addons


def test_inspect_distinguishes_catalog_loaded_default_runtime_and_operator(monkeypatch, tmp_path):
    package = tmp_path / "modern_addon.py"
    package.write_text("raise AssertionError('inspection must not import me')\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setitem(
        sys.modules,
        "addon_utils",
        SimpleNamespace(
            modules=lambda refresh=False: [],
            check=lambda name: (True, False),
        ),
    )
    operator = SimpleNamespace(get_rna_type=lambda: SimpleNamespace(identifier="MMD_OT_import_model"))
    monkeypatch.setitem(
        sys.modules,
        "bpy",
        SimpleNamespace(
            ops=SimpleNamespace(
                mmd_tools=SimpleNamespace(import_model=operator),
            )
        ),
    )
    result = get_addon_status("modern_addon", operators=["mmd_tools.import_model"])
    assert result["success"], result
    status = result["context"]
    assert status["catalog_member"] is False
    assert status["installed"] is False
    assert status["discoverable"] is True
    assert status["loaded"] is False
    assert status["enabled_default"] is True
    assert status["enabled_runtime"] is False
    assert status["operators"] == {"mmd_tools.import_model": True}
    assert "modern_addon" not in sys.modules


def test_enable_disable_changes_runtime_only_and_reports_preference_save_failure(monkeypatch):
    state = {"default": False, "runtime": False}

    def enable(name, default_set=False, persistent=False, handle_error=None):
        state["runtime"] = True
        if default_set:
            state["default"] = True
        return SimpleNamespace(__name__=name)

    def disable(name, default_set=False, handle_error=None):
        state["runtime"] = False
        if default_set:
            state["default"] = False

    monkeypatch.setitem(
        sys.modules,
        "addon_utils",
        SimpleNamespace(
            modules=lambda refresh=False: [],
            check=lambda name: (state["default"], state["runtime"]),
            enable=enable,
            disable=disable,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "bpy",
        SimpleNamespace(
            ops=SimpleNamespace(
                wm=SimpleNamespace(
                    save_userpref=lambda: {"CANCELLED"},
                )
            )
        ),
    )
    result = enable_addon("wiggle_2")
    assert result["success"], result
    assert result["context"]["after"]["enabled_runtime"] is True
    assert result["context"]["after"]["enabled_default"] is False
    assert disable_addon("wiggle_2")["success"]
    assert state == {"default": False, "runtime": False}
    result = enable_addon("wiggle_2", persist=True)
    assert result["success"] is False
    assert result["context"]["after"]["enabled_runtime"] is True
    assert result["context"]["preferences_saved"] is False


def test_control_addon_cannot_disable_itself():
    result = disable_addon("bl_ext.local.dcc_mcp_blender")
    assert result["success"] is False


@pytest.mark.parametrize("name", ["MBTs-NG", "MBTs-NGDLC", "Another-Legacy-Addon"])
def test_hyphenated_legacy_identity_can_be_inspected_enabled_and_disabled(monkeypatch, tmp_path, name):
    path = tmp_path / (name + ".py")
    path.write_text("raise RuntimeError('inspection must not import me')\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    state = {"runtime": False}

    def enable(module_name, **kwargs):
        assert module_name == name
        state["runtime"] = True
        return SimpleNamespace(__name__=name)

    def disable(module_name, **kwargs):
        assert module_name == name
        state["runtime"] = False

    monkeypatch.setitem(
        sys.modules,
        "addon_utils",
        SimpleNamespace(
            modules=lambda refresh=False: [],
            check=lambda module_name: (False, state["runtime"]),
            enable=enable,
            disable=disable,
        ),
    )
    monkeypatch.setitem(sys.modules, "bpy", SimpleNamespace())
    status = get_addon_status(name)
    assert status["success"], status
    assert status["context"]["discoverable"] is True
    assert name not in sys.modules
    assert enable_addon(name)["success"]
    assert get_addon_status(name)["context"]["enabled_runtime"] is True
    assert disable_addon(name)["success"]
    assert get_addon_status(name)["context"]["enabled_runtime"] is False


@pytest.mark.parametrize("name", ["../addon", "addon/path", "addon\\path", "a..b", ".a", "a.", "a()", "a['b']", ""])
def test_invalid_addon_identity_is_rejected_by_all_lifecycle_seams(monkeypatch, name):
    monkeypatch.setitem(sys.modules, "addon_utils", SimpleNamespace())
    for function in (get_addon_status, enable_addon, disable_addon):
        result = function(name)
        assert result["success"] is False
        assert result["error"] == "ValueError", result


def test_catalog_listing_is_bounded_and_operator_absence_is_not_registration(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "addon_utils",
        SimpleNamespace(
            modules=lambda refresh=False: [SimpleNamespace(__name__=name) for name in ("a", "b", "c")],
            check=lambda name: (False, False),
        ),
    )
    monkeypatch.setitem(sys.modules, "bpy", SimpleNamespace(ops=SimpleNamespace()))
    page = list_addons(limit=1, offset=1)
    assert page["success"], page
    assert page["context"]["total"] == 3
    assert [addon["addon_module"] for addon in page["context"]["addons"]] == ["b"]
    assert list_addons(limit=501)["success"] is False
    status = get_addon_status("a", operators=["mmd_tools.import_model"])
    assert status["context"]["operators"] == {"mmd_tools.import_model": False}
