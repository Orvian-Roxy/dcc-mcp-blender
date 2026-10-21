"""Configured NPR add-ons are ready before a launch is reported usable."""

from unittest.mock import patch

from tests.test_npr_runtime import launcher


def test_startup_enables_configured_addon_with_private_preferences():
    config = {"addons": [{"module": "mmd_tools", "operators": ["mmd_tools.import_model"]}]}
    result = {"success": True, "context": {"after": {"enabled_runtime": True}}}
    with patch("dcc_mcp_blender._dev_ops.enable_addon", return_value=result) as enable:
        assert launcher().prepare_addons(config) == {"mmd_tools": result}
    enable.assert_called_once_with("mmd_tools", persist=True, operators=["mmd_tools.import_model"])


def test_addon_failure_is_returned_for_recovery():
    failure = {"success": False, "error": "registration failed"}
    with patch("dcc_mcp_blender._dev_ops.enable_addon", return_value=failure):
        assert launcher().prepare_addons({"addons": [{"module": "missing"}]}) == {"missing": failure}


def test_generic_startup_has_no_addon_requirement():
    with patch("dcc_mcp_blender._dev_ops.enable_addon") as enable:
        assert launcher().prepare_addons({}) == {}
    enable.assert_not_called()
