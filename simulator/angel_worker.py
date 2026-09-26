"""
Runs the real AngelOneClient inside a dedicated child process.

Why: SmartApi's SmartConnect() construction has an intermittent hang on
some hosts (repeatedly observed on Render's free tier) that a thread-based
timeout cannot reliably interrupt - a thread stuck there can hold the GIL
badly enough that even the watchdog thread waiting on its own timeout never
gets scheduled to notice time has passed (confirmed directly: a 45s
in-process timeout sat stuck for 140s+ with no error ever raised). A child
OS process has no such problem - it can always be killed outright with
process.terminate()/.kill() regardless of what it's stuck on internally,
and killing it can never affect the parent process's own responsiveness
(Flask/'/health' stay alive no matter what the child is doing, since they
run in a completely separate process with their own GIL).

Protocol (see AngelOneClientProxy in angel_data.py for the parent side):
parent sends ("call", method_name, args, kwargs) or ("shutdown", None, None, None)
on request_q; child replies with ("ok", result) or ("error", message) on
response_q, plus an initial ("ready", client_code) or ("startup_error", msg)
right after construction. Strictly one call in flight at a time - matches
how TradingEngine's single background tick thread already uses
AngelOneClient (sequential, never concurrent), so no call-id/multiplexing
is needed.
"""

from __future__ import annotations

import multiprocessing


def _worker_main(request_q: "multiprocessing.Queue", response_q: "multiprocessing.Queue") -> None:
    # Imported here, not at module level - only the child process needs
    # SmartApi/logzero imported and patched; the parent process (Flask,
    # gunicorn) never touches any of that directly.
    from angel_data import AngelOneClient

    try:
        client = AngelOneClient()
    except Exception as exc:
        response_q.put(("startup_error", f"{type(exc).__name__}: {exc}"))
        return
    response_q.put(("ready", client.client_code))

    while True:
        msg = request_q.get()
        if msg is None or msg[0] == "shutdown":
            return
        _, method, args, kwargs = msg
        try:
            result = getattr(client, method)(*args, **kwargs)
            response_q.put(("ok", result))
        except Exception as exc:
            response_q.put(("error", f"{type(exc).__name__}: {exc}"))
