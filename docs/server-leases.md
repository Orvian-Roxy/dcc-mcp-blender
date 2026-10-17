# Sharing the Blender service

`acquire_server` adds explicit lifetime ownership to the existing canonical
Blender package singleton. It uses the same Core server, public start/stop APIs,
dispatcher, gateway, and network defaults. It does not start a service per view.

```python
import dcc_mcp_blender

# Run on Blender's main thread. A process-scoped broker retains this handle.
host = dcc_mcp_blender.acquire_server()

# Each view has a separate borrow. The broker's handle is not a view handle.
first_view = dcc_mcp_blender.acquire_server()
second_view = dcc_mcp_blender.acquire_server()
assert first_view.server is second_view.server is host.server

first_view.release()  # Never stops the server.
second_view.release()
host.release()  # Stops only if this broker created the server.
```

## Ownership and state

The creation winner receives an owner lease. Every acquisition of an existing
server receives a borrow, including when an add-on or a legacy `start_server`
caller created that server. No acquisition takes ownership of an external
service. Handle properties are read-only: `server`, `generation`, `owns_server`,
`released`, `valid`, `pending_shutdown`, and `shutdown_ready`. `released` records
an explicit completed release; force revocation makes `valid` false immediately,
while `released` stays false until that stale handle's `release()` is called.

An owner is matched by exact object, generation, and an opaque token; stale
handles cannot stop or change borrower accounting for a replacement. Every
borrow has its own token. There is no destructor or garbage-collection cleanup.
Leases govern cooperative lifetime management, not access control against Python
code that can invoke methods on the server itself.

The lifecycle is `EMPTY -> STARTING -> READY -> STOPPING -> EMPTY`.
Pending owner release uses `CLOSING`; failed teardown uses `FAILED_CLEANUP`.
`get_server()` exposes only a running `READY` instance. Constructor, skill
registration, start, and stop execute outside the coordination lock.

### Pending shutdown is an obligation

An owner release returns `False` while borrows remain. The owner is still valid,
`released` is false, and `pending_shutdown` is true. New acquisitions and legacy
starts are rejected while shutdown is pending, so new views cannot postpone it
indefinitely. Existing borrows remain responsible for their own release.

The last borrow release only updates protected state. It does not call stop,
run callbacks, install a timer, or marshal work to Blender. It makes
`owner.shutdown_ready` true. The broker must keep the owner independently of
any destroyed view and retry from its **already established main-thread tick**:

```python
# In the broker's existing Blender-main-thread lifecycle tick:
if retained_owner.pending_shutdown and retained_owner.shutdown_ready:
    try:
        complete = retained_owner.release()
    except Exception:
        report_cleanup_error()  # Keep the owner and retry according to policy.
    else:
        if complete:
            retained_owner = None
```

A view releases its borrow only after its own routes, subscriptions, and pending
requests have been withdrawn or drained. The host lease is not a per-request
counter and does not replace `BackendSession` cleanup or view-generation fencing.

This helper does not install that broker/tick or connect AuroraView close events.
It is not sufficient to put an owner in a view's local variable and discard it
after a false return or an exception. A future broker integration must prove the
last-view-close retry and failed-cleanup retry against the real host.

Successful release returns `True`. Duplicate or revoked-handle release is a
successful no-op. Cleanup errors propagate; the exact instance and owner remain
pending for retry. A false `is_running` flag does not establish native cleanup.

## Concurrency and failure

`acquire_server` accepts the same positional/keyword creation options as
`start_server`, plus keyword-only `wait_timeout` (default 5 seconds). Options
apply only to a newly created service. Existing reuse preserves the legacy
rule: ordinary options do not reconfigure it, and explicit remote gateway
options are rejected. No listener, authentication, or environment defaults are
changed.

Worker threads can borrow a ready service and wait a bounded time for an active
start/stop attempt. A timeout does not cancel the creator. Every waiter observes
a failed attempt as an error; a later explicit call can retry after cleanup.
Zero timeout means no waiting. Non-finite, negative, or nonnumeric values fail
before state changes. Reentrant calls and main-thread waits fail immediately.
Real Blender creation and teardown require Python's host main thread before any
transition; merely winning a race does not make a worker the host thread.

If registration/start fails after construction, the helper attempts cleanup on
that exact object. `BlenderMcpServer.start` may already have attempted teardown;
the helper records the outcome of its own additional attempt. If cleanup fails,
the object remains hidden in `FAILED_CLEANUP`; `stop_server()` must retry before
anything can replace it. Constructor partial-initialization cleanup remains the
constructor's responsibility because no completed object was returned.

Direct `server.stop()` bypasses lease coordination. A nonrunning or terminally
closed object is hidden
by `get_server`, and a later acquisition refuses to replace it until helper
cleanup succeeds. In particular, a failed direct stop that still reports HTTP running cannot silently
be mistaken for clean shutdown. Native timer removal has its own acceptance
requirements; a passing fake-server state test does not prove timer cleanup.

## Legacy and import boundaries

- `start_server`, `get_server`, and `stop_server` keep their public signatures.
  Normal start is idempotent and returns the raw server. Empty stop is a no-op.
- Legacy-created services remain legacy-owned. Releasing their final borrow
  never stops them. A legacy start of a lease-owned service is an **untracked raw
  reference** and cannot keep it alive after its owner releases.
- `stop_server()` is an explicit global **force stop**. It revokes all leases
  before teardown, even if teardown then fails. Failed force-stop cleanup is
  retried with `stop_server`, not a revoked owner. The existing add-on may use
  this force path when replacing a service it did not establish.
- Previously unspecified concurrent/partial-start behavior is intentionally
  tightened: transitional instances are hidden, and failed-cleanup instances
  cannot be overwritten.
- Normal public-wheel and extension aliases resolve to the same canonical
  server module and manager. Manually loading another package copy, purging its
  namespace, or reloading lifecycle modules while live is outside this guarantee.
  A consumer must retain and use the canonical host package, not create another
  independently imported singleton. This is not a cross-namespace registry.

## Validation boundary

`tests/test_server_leases.py` is a dependency-free, deterministic fake-server
state/concurrency suite. `tests/test_server_lease_integration.py` verifies public
helper argument forwarding, failure stages, and thread-policy wiring with fake
servers using normal Core imports. Public import tests verify alias identity and
shared accounting. None of these establish real Blender rendering, native timer
cleanup, GUI/WebView input, or an installed AuroraView broker.
