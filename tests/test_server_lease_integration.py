"""Public helper wiring against fake servers; Core imports use normal CI deps."""

from __future__ import annotations

import sys
import threading
from types import SimpleNamespace

import pytest


@pytest.fixture()
def singleton(monkeypatch):
    from dcc_mcp_blender import server

    manager = server._ServerLeaseManager(server._require_blender_main_thread)
    monkeypatch.setattr(server, "_server_manager", manager)
    created = []

    class FakeServer:
        def __init__(self, **options):
            self.options = options
            self.is_running = False
            self.calls = []
            created.append(self)

        def register_builtin_actions(self, **options):
            self.calls.append(("register", options))

        def enable_hot_reload(self):
            self.calls.append(("hot_reload", None))

        def start(self):
            self.calls.append(("start", None))
            self.is_running = True

        def stop(self):
            self.calls.append(("stop", None))
            self.is_running = False

    monkeypatch.setattr(server, "BlenderMcpServer", FakeServer)
    yield server, created, FakeServer
    manager.stop()


def test_acquire_preserves_start_positionals_and_configuration(singleton):
    module, created, _ = singleton
    owner = module.acquire_server(
        0,
        ["skills"],
        True,
        False,
        True,
        19765,
        "private-registry",
        dispatcher=object(),
        gateway_remote_host="127.0.0.1",
        gateway_remote_port=0,
    )
    assert owner.server is created[0]
    assert created[0].options["port"] == 0
    assert created[0].options["gateway_port"] == 19765
    assert created[0].options["registry_dir"] == "private-registry"
    assert created[0].options["gateway_remote_host"] == "127.0.0.1"
    assert created[0].options["gateway_remote_port"] == 0
    assert created[0].calls == [("register", {"include_bundled": False}), ("hot_reload", None), ("start", None)]
    assert module.start_server() is owner.server
    assert module.get_server() is owner.server
    with pytest.raises(RuntimeError, match="remote gateway"):
        module.acquire_server(gateway_remote_port=0)
    owner.release()


def test_default_acquire_does_not_expand_network_options(singleton):
    module, created, _ = singleton
    owner = module.acquire_server(register_builtins=False)
    for name in ("port", "gateway_port", "gateway_remote_host", "gateway_remote_port", "enable_gateway_failover"):
        assert created[0].options[name] is None
    assert created[0].calls == [("start", None)]
    owner.release()


def test_acquire_validates_unknown_arguments_before_creation(singleton):
    module, created, _ = singleton
    with pytest.raises(TypeError):
        module.acquire_server(unknown_option=True)
    assert created == []


@pytest.mark.parametrize("stage", ["register_builtin_actions", "enable_hot_reload", "start"])
def test_each_setup_failure_retains_no_published_server(singleton, monkeypatch, stage):
    module, created, fake_type = singleton

    def fail(self, **kwargs):
        assert module.get_server() is None
        raise RuntimeError("setup failed")

    monkeypatch.setattr(fake_type, stage, fail)
    with pytest.raises(RuntimeError, match="setup failed"):
        module.acquire_server(enable_hot_reload=True)
    assert module.get_server() is None
    assert created[0].calls[-1] == ("stop", None)


def test_real_host_affinity_guard_precedes_creation_or_revocation(singleton, monkeypatch):
    module, created, _ = singleton
    # This is only a host-presence/thread-policy test, not execution inside bpy.
    monkeypatch.setitem(sys.modules, "bpy", SimpleNamespace())
    errors = []

    def worker(callback):
        try:
            callback()
        except Exception as exc:
            errors.append(exc)

    def run(callback):
        thread = threading.Thread(target=worker, args=(callback,))
        thread.start()
        thread.join(3)
        assert not thread.is_alive()

    run(lambda: module.acquire_server(register_builtins=False))
    assert len(errors) == 1
    assert "main thread" in str(errors[-1])
    assert created == []
    owner = module.acquire_server(register_builtins=False)
    borrow = []
    run(lambda: borrow.append(module.acquire_server()))
    assert len(borrow) == 1
    run(owner.release)
    run(module.stop_server)
    assert len(errors) == 3
    assert owner.valid and borrow[0].valid
    assert not owner.pending_shutdown
    run(borrow[0].release)
    assert len(errors) == 3
    owner.release()
