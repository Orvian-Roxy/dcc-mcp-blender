"""Dependency-free lifecycle contracts with fake servers, not native acceptance.

Also collected by pytest in the normal repository matrix. For an isolated
source check without Core: python -m unittest discover -s tests -p test_server_leases.py
"""

from __future__ import annotations

import importlib.util
import threading
import unittest
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / "src/dcc_mcp_blender/_server_leases.py"
_SPEC = importlib.util.spec_from_file_location("blender_lease_contract", _PATH)
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


class FakeServer:
    def __init__(self):
        self.is_running = False
        self.starts = 0
        self.stops = []
        self.stop_failures = 0

    def start(self):
        self.starts += 1
        self.is_running = True

    def stop(self):
        self.stops.append(threading.get_ident())
        self.is_running = False
        if self.stop_failures:
            self.stop_failures -= 1
            raise RuntimeError("native cleanup failed after HTTP stopped")


class TestServerLeases(unittest.TestCase):
    def setUp(self):
        self.host_calls = []
        self.manager = _MODULE._ServerLeaseManager(lambda: self.host_calls.append(threading.get_ident()))
        self.created = []

    def create(self):
        server = FakeServer()
        self.created.append(server)
        return server

    def obtain(self, lease=True, **overrides):
        arguments = dict(
            create=self.create, start=lambda server: server.start(), lease=lease, wait_timeout=2, remote_requested=False
        )
        arguments.update(overrides)
        return self.manager.obtain(**arguments)

    def launch(self, callback):
        result, errors = [], []

        def run():
            try:
                result.append(callback())
            except BaseException as exc:
                errors.append(exc)

        thread = threading.Thread(target=run)
        thread.start()
        return thread, result, errors

    def joined(self, task):
        thread, result, errors = task
        thread.join(3)
        self.assertFalse(thread.is_alive(), "lifecycle thread did not finish")
        return result, errors

    def test_owner_and_borrow_release(self):
        owner = self.obtain()
        borrow = self.obtain()
        self.assertTrue(owner.owns_server)
        self.assertFalse(borrow.owns_server)
        self.assertIs(owner.server, borrow.server)
        self.assertEqual(owner.generation, borrow.generation)
        self.assertTrue(borrow.release())
        self.assertTrue(borrow.release())
        self.assertEqual(owner.server.stops, [])
        self.assertTrue(owner.release())
        self.assertTrue(owner.release())
        self.assertEqual(len(owner.server.stops), 1)
        self.assertIsNone(self.manager.get_server())

    def test_legacy_created_server_is_never_adopted(self):
        server = self.obtain(lease=False)
        first, second = self.obtain(), self.obtain()
        self.assertFalse(first.owns_server)
        self.assertFalse(second.owns_server)
        first.release()
        second.release()
        self.assertIs(self.manager.get_server(), server)
        self.assertEqual(server.stops, [])
        self.manager.stop()
        self.assertEqual(len(server.stops), 1)

    def test_legacy_reuse_is_untracked_and_does_not_transfer_owner(self):
        owner = self.obtain()
        self.assertIs(self.obtain(lease=False), owner.server)
        self.assertTrue(owner.release())
        self.assertEqual(len(owner.server.stops), 1)

    def test_pending_owner_and_last_worker_borrow_need_main_thread_retry(self):
        owner = self.obtain()
        borrow = self.obtain()
        self.assertFalse(owner.release())
        self.assertTrue(owner.pending_shutdown)
        self.assertFalse(owner.shutdown_ready)
        self.assertFalse(owner.released)
        self.assertIsNone(self.manager.get_server())
        with self.assertRaisesRegex(RuntimeError, "shutdown is pending"):
            self.obtain()
        before = list(self.host_calls)
        result, errors = self.joined(self.launch(borrow.release))
        self.assertEqual(result, [True])
        self.assertEqual(errors, [])
        self.assertEqual(self.host_calls, before)
        self.assertEqual(owner.server.stops, [])
        self.assertTrue(owner.shutdown_ready)
        self.assertTrue(owner.release())
        self.assertEqual(owner.server.stops, [threading.get_ident()])
        self.assertFalse(owner.pending_shutdown)

    def test_failed_owner_cleanup_is_retained_and_retryable(self):
        owner = self.obtain()
        owner.server.stop_failures = 1
        with self.assertRaisesRegex(RuntimeError, "native cleanup"):
            owner.release()
        self.assertFalse(owner.server.is_running)
        self.assertTrue(owner.valid)
        self.assertFalse(owner.released)
        self.assertTrue(owner.pending_shutdown)
        self.assertTrue(owner.shutdown_ready)
        self.assertIsNone(self.manager.get_server())
        with self.assertRaisesRegex(RuntimeError, "shutdown is pending"):
            self.obtain()
        self.assertEqual(len(self.created), 1)
        owner.release()
        self.assertEqual(len(owner.server.stops), 2)
        self.assertFalse(owner.valid)

    def test_force_stop_revokes_all_leases_even_when_cleanup_fails(self):
        owner, borrow = self.obtain(), self.obtain()
        owner.server.stop_failures = 1
        with self.assertRaises(RuntimeError):
            self.manager.stop()
        self.assertFalse(owner.valid)
        self.assertFalse(borrow.valid)
        self.assertTrue(owner.release())
        self.assertTrue(borrow.release())
        self.assertEqual(len(owner.server.stops), 1)
        with self.assertRaises(RuntimeError):
            self.obtain()
        self.manager.stop()
        self.assertEqual(len(owner.server.stops), 2)
        self.manager.stop()

    def test_stale_handles_cannot_stop_or_decrement_replacement(self):
        old_owner, old_borrow = self.obtain(), self.obtain()
        self.manager.stop()
        owner, borrow = self.obtain(), self.obtain()
        self.assertGreater(owner.generation, old_owner.generation)
        old_owner.release()
        old_borrow.release()
        self.assertEqual(owner.server.stops, [])
        self.assertFalse(owner.release())
        borrow.release()
        owner.release()

    def test_forged_owner_token_has_no_authority(self):
        owner = self.obtain()
        forged = _MODULE.BlenderServerLease(self.manager, owner.server, owner.generation, object(), True)
        self.assertFalse(forged.valid)
        self.assertTrue(forged.release())
        self.assertTrue(owner.valid)
        self.assertEqual(owner.server.stops, [])
        owner.release()

    def test_remote_reconfiguration_keeps_existing_rejection(self):
        owner = self.obtain()
        with self.assertRaisesRegex(RuntimeError, "remote gateway"):
            self.obtain(remote_requested=True)
        self.assertIs(self.manager.get_server(), owner.server)
        owner.release()

    def test_read_only_public_handle_properties(self):
        owner = self.obtain()
        for name in ("server", "generation", "owns_server", "released", "valid", "pending_shutdown", "shutdown_ready"):
            with self.subTest(name=name), self.assertRaises(AttributeError):
                setattr(owner, name, None)
        owner.release()

    def test_creation_thread_policy_rejects_before_state_mutation(self):
        def reject():
            raise RuntimeError("not host main thread")

        self.manager._require_host_thread = reject
        with self.assertRaisesRegex(RuntimeError, "host main thread"):
            self.obtain()
        self.assertEqual(self.created, [])
        self.assertEqual(self.manager._generation, 0)
        self.assertIsNone(self.manager.get_server())

    def test_owner_thread_policy_rejects_before_pending_or_force_revocation(self):
        owner, borrow = self.obtain(), self.obtain()

        def reject():
            raise RuntimeError("not host main thread")

        self.manager._require_host_thread = reject
        for callback in (owner.release, self.manager.stop):
            with self.assertRaises(RuntimeError):
                callback()
            self.assertTrue(owner.valid)
            self.assertTrue(borrow.valid)
            self.assertFalse(owner.pending_shutdown)
            self.assertIs(self.manager.get_server(), owner.server)
        borrow.release()  # no host-thread policy is needed for borrowed release
        self.manager._require_host_thread = lambda: None
        owner.release()

    def test_constructor_failure_can_retry_without_publishing(self):
        def fail():
            self.assertIsNone(self.manager.get_server())
            raise ValueError("constructor failed")

        with self.assertRaisesRegex(ValueError, "constructor failed"):
            self.obtain(create=fail)
        self.assertIsNone(self.manager.get_server())
        owner = self.obtain()
        self.assertEqual(owner.generation, 2)
        owner.release()

    def test_setup_failure_cleans_returned_instance_then_allows_retry(self):
        def fail(server):
            self.assertIsNone(self.manager.get_server())
            raise ValueError("registration failed")

        with self.assertRaisesRegex(ValueError, "registration failed"):
            self.obtain(start=fail)
        self.assertEqual(len(self.created[0].stops), 1)
        self.assertIsNone(self.manager.get_server())
        self.obtain().release()

    def test_failed_startup_cleanup_blocks_replacement_until_force_retry(self):
        def fail(server):
            server.stop_failures = 1
            raise ValueError("startup failed")

        with self.assertRaisesRegex(RuntimeError, "startup and cleanup failed"):
            self.obtain(start=fail)
        self.assertIsNone(self.manager.get_server())
        with self.assertRaisesRegex(RuntimeError, "shutdown is pending"):
            self.obtain()
        self.assertEqual(len(self.created), 1)
        self.manager.stop()
        self.assertEqual(len(self.created[0].stops), 2)
        self.obtain().release()

    def test_direct_stop_requires_observed_helper_cleanup_before_replacement(self):
        owner = self.obtain()
        owner.server.stop_failures = 1
        with self.assertRaises(RuntimeError):
            owner.server.stop()
        self.assertIsNone(self.manager.get_server())
        with self.assertRaisesRegex(RuntimeError, "shutdown is pending"):
            self.obtain()
        self.assertEqual(len(self.created), 1)
        owner.release()
        self.assertEqual(len(owner.server.stops), 2)
        self.obtain().release()

    def test_early_direct_stop_failure_is_hidden_even_while_http_running(self):
        owner = self.obtain()
        original_stop = owner.server.stop

        def early_failure():
            owner.server._closed = True
            raise RuntimeError("stop failed before HTTP stopped")

        owner.server.stop = early_failure
        with self.assertRaisesRegex(RuntimeError, "before HTTP"):
            owner.server.stop()
        self.assertTrue(owner.server.is_running)
        self.assertIsNone(self.manager.get_server())
        with self.assertRaisesRegex(RuntimeError, "shutdown is pending"):
            self.obtain()
        self.assertEqual(len(self.created), 1)
        self.assertTrue(owner.shutdown_ready)
        owner.server.stop = original_stop
        owner.release()
        replacement = self.obtain()
        self.assertIsNot(replacement.server, owner.server)
        replacement.release()

    def test_closed_start_result_is_cleaned_not_published(self):
        def start(server):
            server.start()
            server._closed = True

        with self.assertRaisesRegex(RuntimeError, "without a ready server"):
            self.obtain(start=start)
        self.assertIsNone(self.manager.get_server())
        self.assertEqual(len(self.created[0].stops), 1)
        self.obtain().release()

    def test_concurrent_acquire_has_one_owner_and_one_constructor(self):
        self._concurrent_start(creator_lease=True)

    def test_mixed_legacy_start_and_acquire_remains_legacy_owned(self):
        self._concurrent_start(creator_lease=False)

    def _concurrent_start(self, creator_lease):
        entered, finish, waiting = threading.Event(), threading.Event(), threading.Event()
        old_wait = self.manager._wait

        def wait(deadline):
            waiting.set()
            old_wait(deadline)

        self.manager._wait = wait

        def start(server):
            entered.set()
            self.assertTrue(finish.wait(2))
            server.start()

        creator = self.launch(lambda: self.obtain(lease=creator_lease, start=start))
        self.assertTrue(entered.wait(2))
        self.assertIsNone(self.manager.get_server())
        waiter = self.launch(self.obtain)
        self.assertTrue(waiting.wait(2))
        with self.assertRaisesRegex(RuntimeError, "without blocking"):
            self.obtain()  # main thread never waits on a worker's transition
        finish.set()
        first, first_errors = self.joined(creator)
        second, second_errors = self.joined(waiter)
        self.assertEqual(first_errors + second_errors, [])
        self.assertEqual(len(self.created), 1)
        self.assertFalse(second[0].owns_server)
        server = first[0].server if creator_lease else first[0]
        self.assertIs(second[0].server, server)
        second[0].release()
        if creator_lease:
            self.assertTrue(first[0].owns_server)
            first[0].release()
        else:
            self.manager.stop()

    def test_startup_failure_wakes_every_waiter_and_new_call_can_retry(self):
        entered, finish, waiting = threading.Event(), threading.Event(), threading.Event()
        wait_count = []
        old_wait = self.manager._wait

        def wait(deadline):
            wait_count.append(1)
            if len(wait_count) == 2:
                waiting.set()
            old_wait(deadline)

        self.manager._wait = wait

        def start(server):
            entered.set()
            self.assertTrue(finish.wait(2))
            raise ValueError("attempt failed")

        creator = self.launch(lambda: self.obtain(start=start))
        self.assertTrue(entered.wait(2))
        waiters = [self.launch(self.obtain), self.launch(self.obtain)]
        self.assertTrue(waiting.wait(2))
        finish.set()
        _, errors = self.joined(creator)
        self.assertIsInstance(errors[0], ValueError)
        for waiter in waiters:
            result, errors = self.joined(waiter)
            self.assertEqual(result, [])
            self.assertIsInstance(errors[0], RuntimeError)
            self.assertIsInstance(errors[0].__cause__, ValueError)
        self.assertEqual(len(self.created), 1)
        self.obtain().release()

    def test_reentrant_start_and_stop_fail_without_deadlock(self):
        def start(server):
            for callback in (self.obtain, self.manager.stop):
                with self.assertRaisesRegex(RuntimeError, "busy"):
                    callback()
            server.start()

        owner = self.obtain(start=start)
        original_stop = owner.server.stop

        def stop():
            self.assertIsNone(self.manager.get_server())
            with self.assertRaisesRegex(RuntimeError, "in progress"):
                owner.release()
            with self.assertRaisesRegex(RuntimeError, "busy"):
                self.obtain()
            original_stop()

        owner.server.stop = stop
        owner.release()

    def test_zero_worker_wait_timeout_does_not_cancel_creator(self):
        entered, finish = threading.Event(), threading.Event()

        def start(server):
            entered.set()
            self.assertTrue(finish.wait(2))
            server.start()

        creator = self.launch(lambda: self.obtain(start=start))
        self.assertTrue(entered.wait(2))
        result, errors = self.joined(self.launch(lambda: self.obtain(wait_timeout=0)))
        self.assertEqual(result, [])
        self.assertIsInstance(errors[0], TimeoutError)
        finish.set()
        result, errors = self.joined(creator)
        self.assertEqual(errors, [])
        result[0].release()

    def test_concurrent_duplicate_borrow_release_cannot_underflow(self):
        owner, borrow = self.obtain(), self.obtain()
        tasks = [self.launch(borrow.release) for _ in range(8)]
        for task in tasks:
            result, errors = self.joined(task)
            self.assertEqual(result, [True])
            self.assertEqual(errors, [])
        self.assertEqual(owner.server.stops, [])
        self.assertTrue(owner.release())
        self.assertEqual(len(owner.server.stops), 1)

    def test_stop_failure_wakes_waiter_without_creating_replacement(self):
        owner = self.obtain()
        entered, finish, waiting = threading.Event(), threading.Event(), threading.Event()
        original_stop = owner.server.stop
        old_wait = self.manager._wait

        def wait(deadline):
            waiting.set()
            old_wait(deadline)

        self.manager._wait = wait

        def stop():
            entered.set()
            self.assertTrue(finish.wait(2))
            original_stop()
            raise ValueError("stop failed")

        owner.server.stop = stop
        stopping = self.launch(owner.release)
        self.assertTrue(entered.wait(2))
        self.assertIsNone(self.manager.get_server())
        waiter = self.launch(self.obtain)
        self.assertTrue(waiting.wait(2))
        finish.set()
        _, errors = self.joined(stopping)
        self.assertIsInstance(errors[0], ValueError)
        result, errors = self.joined(waiter)
        self.assertEqual(result, [])
        self.assertIsInstance(errors[0].__cause__, ValueError)
        self.assertTrue(owner.shutdown_ready)
        self.assertEqual(len(self.created), 1)
        owner.server.stop = original_stop
        owner.release()

    def test_nonrunning_start_result_is_cleaned_not_published(self):
        with self.assertRaisesRegex(RuntimeError, "without a ready server"):
            self.obtain(start=lambda server: None)
        self.assertIsNone(self.manager.get_server())
        self.assertEqual(len(self.created[0].stops), 1)
        self.obtain().release()

    def test_wait_timeout_validation_precedes_mutation(self):
        for value in (-1, True, None, "1", float("nan"), float("inf"), 10**1000):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.obtain(wait_timeout=value)
        self.assertEqual(self.created, [])


if __name__ == "__main__":
    unittest.main()
