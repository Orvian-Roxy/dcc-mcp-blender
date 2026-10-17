"""Atomic ownership for the canonical Blender package's singleton.

This module has no Core or bpy imports. It coordinates existing public server
lifecycle calls; it does not implement a transport, dispatcher, or gateway.
"""

from __future__ import annotations

import math
import threading
import time
from typing import Any, Callable, Optional


def _server_ready(server: Any) -> bool:
    # The adapter sets _closed before starting teardown. HTTP can still be
    # running after an early failure; never lend that terminal instance again.
    return getattr(server, "_closed", False) is not True and bool(server.is_running)


class BlenderServerLease:
    """An exact-generation owner or borrow returned by ``acquire_server``.

    Only a creator's lease can stop its server. A pending owner must be retained
    by a broker independently of any view and retried on Blender's main thread.
    Borrow release never performs host work, including when it is the last one.
    This is a lifetime contract, not a security boundary around Python objects.
    """

    def __init__(self, manager: Any, server: Any, generation: int, token: object, owns: bool) -> None:
        self._manager = manager
        self._server = server
        self._generation = generation
        self._token = token
        self._owns = owns
        self._released = False

    @property
    def server(self) -> Any:
        """The original server, even after this handle is invalidated."""
        return self._server

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def owns_server(self) -> bool:
        return self._owns

    @property
    def released(self) -> bool:
        return self._released

    @property
    def valid(self) -> bool:
        """Whether this handle still belongs to the current managed instance."""
        return self._manager.lease_status(self)[0]

    @property
    def pending_shutdown(self) -> bool:
        """Owner release was requested but cleanup has not completed."""
        return self._manager.lease_status(self)[1]

    @property
    def shutdown_ready(self) -> bool:
        """A pending owner has no borrows and can retry on the host main thread."""
        return self._manager.lease_status(self)[2]

    def release(self) -> bool:
        """Release this handle; False means the owner still has live borrows.

        Cleanup errors propagate and leave a pending, retryable owner. A stale
        or already released handle is an idempotent no-op. Never discard an
        owner after False or an error: its broker must drive a later retry.
        """
        return self._manager.release(self)


class _Operation:
    def __init__(self) -> None:
        self.thread = threading.get_ident()
        self.done = False
        self.error: Optional[BaseException] = None


class _ServerLeaseManager:
    """One canonical module's state machine; all host work runs outside its lock."""

    def __init__(self, require_host_thread: Callable[[], None]) -> None:
        self._condition = threading.Condition(threading.RLock())
        self._require_host_thread = require_host_thread
        self._state = "EMPTY"
        self._server: Any = None
        self._generation = 0
        self._owner: Optional[object] = None
        self._borrows: set = set()
        self._operation: Optional[_Operation] = None

    def get_server(self) -> Any:
        with self._condition:
            if self._state == "READY" and _server_ready(self._server):
                return self._server
            return None

    def _wait(self, deadline: float) -> None:
        operation = self._operation
        assert operation is not None
        if operation.thread == threading.get_ident() or threading.current_thread() is threading.main_thread():
            raise RuntimeError("Blender server lifecycle is busy; retry from the host loop without blocking")
        while not operation.done:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Timed out waiting for Blender server lifecycle")
            self._condition.wait(min(remaining, threading.TIMEOUT_MAX))
        if operation.error is not None:
            raise RuntimeError("Concurrent Blender server lifecycle failed; retry explicitly") from operation.error

    def obtain(
        self,
        create: Callable[[], Any],
        start: Callable[[Any], None],
        *,
        lease: bool,
        wait_timeout: float,
        remote_requested: bool,
    ) -> Any:
        if isinstance(wait_timeout, bool) or not isinstance(wait_timeout, (int, float)):
            raise ValueError("wait_timeout must be a finite non-negative number")
        try:
            wait_timeout = float(wait_timeout)
        except OverflowError as exc:
            raise ValueError("wait_timeout must be a finite non-negative number") from exc
        if not math.isfinite(wait_timeout) or wait_timeout < 0:
            raise ValueError("wait_timeout must be a finite non-negative number")
        deadline = time.monotonic() + wait_timeout
        with self._condition:
            while self._state in {"STARTING", "STOPPING"}:
                self._wait(deadline)
            if self._state == "READY":
                if not _server_ready(self._server):
                    # Direct server.stop() may have failed after stopping HTTP.
                    # Never infer successful native cleanup from is_running.
                    self._state = "FAILED_CLEANUP"
                else:
                    if remote_requested:
                        raise RuntimeError("Stop the running Blender server before configuring remote gateway options")
                    return self._new_lease(False) if lease else self._server
            if self._state != "EMPTY":
                raise RuntimeError("Blender server shutdown is pending; retry its owner or stop_server first")
            self._require_host_thread()
            self._generation += 1
            operation = self._operation = _Operation()
            self._state = "STARTING"

        server = None
        try:
            # A constructor that raises before returning must clean its own
            # partial initialization. There is no completed object to retain.
            server = create()
            start(server)
            if not _server_ready(server):
                raise RuntimeError("Blender server startup returned without a ready server")
        except BaseException as error:
            cleanup_error = None
            if server is not None:
                try:
                    server.stop()
                except BaseException as exc:
                    cleanup_error = exc
            with self._condition:
                self._server = server if cleanup_error is not None else None
                self._state = "FAILED_CLEANUP" if cleanup_error is not None else "EMPTY"
                operation.error = error
                operation.done = True
                self._condition.notify_all()
            if cleanup_error is not None:
                raise RuntimeError(
                    "Blender startup and cleanup failed; stop_server must retry exact-instance cleanup"
                ) from cleanup_error
            raise

        with self._condition:
            self._server = server
            self._state = "READY"
            result = self._new_lease(True) if lease else server
            operation.done = True
            self._condition.notify_all()
            return result

    def _new_lease(self, owns: bool) -> BlenderServerLease:
        token = object()
        if owns:
            self._owner = token
        else:
            self._borrows.add(token)
        return BlenderServerLease(self, self._server, self._generation, token, owns)

    def _matches(self, lease: BlenderServerLease) -> bool:
        return (
            not lease._released
            and lease.server is self._server
            and lease.generation == self._generation
            and (lease._token is self._owner if lease.owns_server else lease._token in self._borrows)
        )

    def lease_status(self, lease: BlenderServerLease) -> tuple:
        with self._condition:
            valid = self._matches(lease)
            pending = valid and lease.owns_server and self._state in {"CLOSING", "STOPPING", "FAILED_CLEANUP"}
            ready = pending and not self._borrows and self._state != "STOPPING"
            return valid, pending, ready

    def release(self, lease: BlenderServerLease) -> bool:
        with self._condition:
            if not self._matches(lease):
                lease._released = True
                return True
            if not lease.owns_server:
                self._borrows.remove(lease._token)
                lease._released = True
                self._condition.notify_all()
                return True
            self._require_host_thread()
            if self._state == "STOPPING":
                raise RuntimeError("Blender server cleanup is already in progress")
            if self._borrows:
                self._state = "CLOSING"
                return False
            server, operation = self._begin_stop()
        self._finish_stop(server, operation)
        lease._released = True
        return True

    def stop(self) -> None:
        """Legacy explicit force-stop; all handles are revoked before teardown."""
        with self._condition:
            if self._state == "EMPTY":
                return
            self._require_host_thread()
            if self._state in {"STARTING", "STOPPING"}:
                raise RuntimeError("Blender server lifecycle is busy; retry stop_server from the host loop")
            self._owner = None
            self._borrows.clear()
            server, operation = self._begin_stop()
        self._finish_stop(server, operation)

    def _begin_stop(self) -> tuple:
        self._state = "STOPPING"
        operation = self._operation = _Operation()
        return self._server, operation

    def _finish_stop(self, server: Any, operation: _Operation) -> None:
        try:
            server.stop()
        except BaseException as error:
            with self._condition:
                self._state = "FAILED_CLEANUP"
                operation.error = error
                operation.done = True
                self._condition.notify_all()
            raise
        with self._condition:
            self._server = None
            self._owner = None
            self._borrows.clear()
            self._state = "EMPTY"
            operation.done = True
            self._condition.notify_all()
