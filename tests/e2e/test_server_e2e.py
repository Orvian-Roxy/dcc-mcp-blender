"""E2E tests for BlenderMcpServer progressive loading and multi-instance gateway.

These tests run inside a real Blender Python interpreter and verify that the
server can:

 * Start and expose the MCP endpoint
 * Discover skills (metadata-only, no script import)
 * Lazy-load a skill on demand
 * Unload a skill and reload it
 * Start a second instance on a different port (multi-instance gateway)

Run::

    blender --background --python -m pytest tests/e2e/test_server_e2e.py -- -v
"""

from __future__ import annotations

import sys

import pytest

bpy = pytest.importorskip("bpy", reason="bpy not available — run inside Blender Python interpreter")

if sys.platform == "win32":
    pytest.skip("server lifecycle is covered by separate-process workflow smoke on Windows", allow_module_level=True)

pytestmark = pytest.mark.e2e


def _new_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def _skill_get(skill, key, default=None):
    if isinstance(skill, dict):
        return skill.get(key, default)
    return getattr(skill, key, default)


# ── Server lifecycle ──────────────────────────────────────────────────────────


class TestServerLifecycleE2E:
    """Start / stop the MCP server inside a real Blender session."""

    def test_start_and_stop(self):
        import dcc_mcp_blender

        server = dcc_mcp_blender.start_server(port=0)
        try:
            assert server.is_running
            url = server.mcp_url
            assert url is not None
            assert url.startswith("http://127.0.0.1:")
        finally:
            dcc_mcp_blender.stop_server()
            assert not server.is_running

    def test_start_idempotent(self):
        import dcc_mcp_blender

        s1 = dcc_mcp_blender.start_server(port=0)
        try:
            s2 = dcc_mcp_blender.start_server(port=0)
            assert s1 is s2
        finally:
            dcc_mcp_blender.stop_server()

    def test_mcp_initialize_handshake(self):
        """Verify the server responds to an MCP initialize request."""
        import json
        import urllib.request

        import dcc_mcp_blender

        server = dcc_mcp_blender.start_server(port=0)
        try:
            url = server.mcp_url
            body = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 0,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "clientInfo": {"name": "e2e-test", "version": "1"},
                    },
                }
            ).encode()
            req = urllib.request.Request(
                url,
                data=body,
                headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
            assert data["result"]["serverInfo"]["name"] == "dcc-mcp-blender"
        finally:
            dcc_mcp_blender.stop_server()


class TestServerLeaseE2E:
    """Actual background-host service ownership, not GUI or view-broker QA."""

    @staticmethod
    def initialize(server):
        import json
        import urllib.request

        request = urllib.request.Request(
            server.mcp_url,
            data=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "clientInfo": {"name": "lease-e2e", "version": "1"},
                    },
                }
            ).encode(),
            headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read())
        assert payload["result"]["serverInfo"]["name"] == "dcc-mcp-blender"

    def setup_method(self):
        import dcc_mcp_blender

        dcc_mcp_blender.stop_server()

    def teardown_method(self):
        import dcc_mcp_blender

        dcc_mcp_blender.stop_server()

    @staticmethod
    def acquire():
        import dcc_mcp_blender

        return dcc_mcp_blender.acquire_server(
            port=0,
            gateway_port=0,
            enable_gateway_failover=False,
            register_builtins=False,
        )

    def test_owner_waits_for_views_and_last_worker_release_preserves_http(self):
        import threading

        import dcc_mcp_blender

        owner = self.acquire()
        first = dcc_mcp_blender.acquire_server()
        last = dcc_mcp_blender.acquire_server()
        assert owner.owns_server and not first.owns_server and not last.owns_server
        assert owner.server is first.server is last.server
        assert first.release()
        self.initialize(last.server)
        assert owner.release() is False
        assert owner.pending_shutdown and not owner.shutdown_ready
        assert dcc_mcp_blender.get_server() is None
        result = []
        worker = threading.Thread(target=lambda: result.append(last.release()))
        worker.start()
        worker.join(5)
        assert not worker.is_alive()
        assert result == [True]
        assert owner.shutdown_ready
        assert owner.server.is_running
        self.initialize(owner.server)
        assert owner.release()
        assert not owner.server.is_running

    def test_last_borrow_of_legacy_service_never_stops_it(self):
        import dcc_mcp_blender

        server = dcc_mcp_blender.start_server(
            port=0,
            gateway_port=0,
            enable_gateway_failover=False,
            register_builtins=False,
        )
        borrow = dcc_mcp_blender.acquire_server()
        assert not borrow.owns_server
        assert borrow.release()
        assert dcc_mcp_blender.get_server() is server
        assert server.is_running
        self.initialize(server)

    def test_revoked_native_handles_cannot_stop_replacement(self):
        import dcc_mcp_blender

        old_owner = self.acquire()
        old_borrow = dcc_mcp_blender.acquire_server()
        dcc_mcp_blender.stop_server()
        owner = self.acquire()
        assert owner.generation > old_owner.generation
        assert old_owner.release() and old_borrow.release()
        assert owner.valid and owner.server.is_running
        self.initialize(owner.server)
        assert owner.release()


# ── Progressive skill loading ─────────────────────────────────────────────────


class TestProgressiveLoadingE2E:
    """discover_skills / load_skill / unload_skill inside real Blender.

    ``create_skill_manager()`` (called by ``start_server()``) may eagerly
    discover AND load all built-in skills at startup.  Every test in this
    class handles both states (already-loaded and not-yet-loaded) so the
    tests are robust regardless of the dcc-mcp-core loading strategy.
    """

    def setup_method(self):
        import dcc_mcp_blender

        dcc_mcp_blender.stop_server()

    def teardown_method(self):
        import dcc_mcp_blender

        dcc_mcp_blender.stop_server()

    def test_list_skills_returns_skills(self):
        import dcc_mcp_blender

        server = dcc_mcp_blender.start_server(port=0)
        skills = server.list_skills()
        assert isinstance(skills, list)
        # create_skill_manager discovers all built-in skills at startup
        assert len(skills) > 0, "Expected at least one skill to be discovered"

    def test_find_skills_by_dcc(self):
        import dcc_mcp_blender

        server = dcc_mcp_blender.start_server(port=0)
        blender_skills = server.find_skills(dcc="blender")
        assert isinstance(blender_skills, list)
        assert len(blender_skills) > 0

    def test_loaded_skill_count_after_start(self):
        """After start(), at least one skill should be available (loaded or discovered)."""
        import dcc_mcp_blender

        server = dcc_mcp_blender.start_server(port=0)
        # Either eagerly loaded or just discovered — list_skills() covers both
        assert len(server.list_skills()) > 0

    def test_unload_and_reload_skill(self):
        """Unload a skill then reload it — verifies round-trip."""
        import dcc_mcp_blender
        from dcc_mcp_blender.host import BlenderCallableDispatcher, BlenderHost

        dispatcher = BlenderCallableDispatcher()
        host = BlenderHost(dispatcher)
        server = dcc_mcp_blender.start_server(port=0, dispatcher=dispatcher)
        host.start()
        try:
            skill_name = "blender-scene"

            if not server.is_skill_loaded(skill_name):
                assert server.load_skill(skill_name) is True
            assert server.is_skill_loaded(skill_name)

            removed = server.unload_skill(skill_name)
            assert removed is True
            assert not server.is_skill_loaded(skill_name)

            loaded_again = server.load_skill(skill_name)
            assert loaded_again is True
            assert server.is_skill_loaded(skill_name)
        finally:
            host.stop()

    def test_discover_extra_paths_no_crash(self):
        """Calling discover_skills() with no extra paths must not raise."""
        import dcc_mcp_blender

        server = dcc_mcp_blender.start_server(port=0)
        # May return 0 if all skills were already discovered at startup
        count = server.discover_skills()
        assert isinstance(count, int)
        assert count >= 0


# ── Multi-instance gateway ────────────────────────────────────────────────────


class TestMultiInstanceGatewayE2E:
    """Two BlenderMcpServer instances can run simultaneously on different ports."""

    def test_two_instances_on_different_ports(self):
        """Start two servers, verify each responds independently."""
        import json
        import urllib.request

        from dcc_mcp_blender.server import BlenderMcpServer

        # This is an in-process HTTP isolation check. Gateway election is covered
        # by separate-process workflow smoke tests and can leave native threads
        # racing Blender teardown on Windows when two elected gateways run inside
        # one interpreter.
        s1 = BlenderMcpServer(port=0, gateway_port=0, enable_gateway_failover=False)
        s2 = BlenderMcpServer(port=0, gateway_port=0, enable_gateway_failover=False)
        s1.start()
        s2.start()
        try:
            assert s1.port != s2.port, "Servers must listen on different ports"
            for server in (s1, s2):
                url = server.mcp_url
                body = json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 0,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2025-03-26",
                            "capabilities": {},
                            "clientInfo": {"name": "e2e-gateway", "version": "1"},
                        },
                    }
                ).encode()
                req = urllib.request.Request(
                    url,
                    data=body,
                    headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read())
                assert data["result"]["serverInfo"]["name"] == "dcc-mcp-blender"
        finally:
            s1.stop()
            s2.stop()
