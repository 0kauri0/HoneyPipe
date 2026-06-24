# Honeypipe

A tiny **actor-based async runtime** for Python. Each *pipe* is an independent
actor with its own message queue and handler map; pipes form a parent/child tree
and communicate by passing plain `dict` messages. The whole core is ~130 lines.

- **Actor model** — one queue per pipe, one coroutine draining it, one handler per message type.
- **Dict-as-protocol** — pipes and messages are plain dicts (a `Pipe` `TypedDict` documents the shape).
- **Hierarchical** — pipes spawn sub-pipes; messages route up to `parent`, down to a named sub-pipe, or to `self`.
- **Runtime-pluggable** — add/remove handlers and spawn sub-pipes while running.
- **HTTP proxy** — an optional FastAPI server bridges remote clients to the pipe tree.

---

## Install

Requires **Python 3.11+** (uses `asyncio.Queue`, `TypedDict`, `X | Y` typing).

```bash
# core runtime has no third-party deps.
# the HTTP server and tests need:
pip install fastapi httpx pytest pytest-asyncio
```

---

## Concepts

### A pipe

A pipe is a dict produced by `create_pipe(...)`:

```python
{
    "data":      {"id": "...", ...},   # arbitrary per-pipe state (must carry an "id")
    "parent":    {"id": ..., "queue": <asyncio.Queue>},  # or {} for a root
    "queue":     <asyncio.Queue>,      # this pipe's inbox
    "sub_pipes": [{"id": ..., "queue": ...}, ...],       # registered children
    "handlers":  {"<type>": <async fn(pipe, msg)>, ...}, # dispatch table
}
```

The shape is captured by `core.Pipe` (a `TypedDict`, `total=False`) — zero runtime
cost, just documentation + editor hints.

### A message

A message is any dict with a `"type"` key. The runtime adds `"id"` and `"src"`
on send. Example: `{"type": "greet", "name": "ada"}`.

### The loop

`run_pipe(pipe)` is the actor loop:

```
while not pipe["data"]["_shutdown"]:
    msg = await pipe["queue"].get()
    await dispatch(pipe, msg)      # validates "type", finds handler, runs it
```

`dispatch` validates input at the queue boundary: a message with no `type`, or an
unknown `type`, is routed to the `_error` handler instead of crashing the loop.
Exceptions raised inside a handler are also caught and funneled to `_error`.

---

## Quick start

```python
import asyncio
from core import create_pipe, send, run_pipe

async def main():
    # a handler is just: async def (pipe, msg)
    async def greet(pipe, msg):
        print(f"hello {msg['name']} (from {msg['src']})")

    pipe = create_pipe(data={"id": "p1"}, handlers={"greet": greet})

    # run the loop in the background
    loop = asyncio.create_task(run_pipe(pipe))

    await send(pipe, {"type": "greet", "name": "ada"})   # -> "self" by default
    await asyncio.sleep(0.05)

    await send(pipe, {"type": "_shutdown"})               # stop the loop
    await loop

asyncio.run(main())
```

---

## Core API (`core.py`)

### `create_pipe(data=None, parent=None, handlers=None, init_seq=None) -> Pipe`
Builds a pipe. Always installs the built-in handlers (below); your `handlers`
are merged on top (and may override built-ins). `init_seq` is a list of messages
pre-loaded onto the queue before the loop starts.

### `async send(pipe, msg, dst=None)`
Delivers `msg` to a target queue. **Does not mutate the caller's dict** — it sends
a copy with `id` and `src` injected, so you can safely reuse a payload.

| `dst` | Target |
|---|---|
| `None` / `"self"` | this pipe's own queue |
| `"parent"` | `pipe["parent"]["queue"]` (raises `RuntimeError` if no parent) |
| `"<sub-id>"` | the matching entry in `pipe["sub_pipes"]` (raises `ValueError` if not found) |

### `async dispatch(pipe, msg)`
Validates `msg["type"]`, looks up the handler, runs it. Routes missing-type,
unknown-type, and handler exceptions to `_error`.

### `async run_pipe(pipe)`
The actor loop. Runs until `pipe["data"]["_shutdown"]` is truthy.

### Built-in handlers
These are registered on every pipe and invoked by message `type`:

| `type` | Message fields | Effect |
|---|---|---|
| `add_handler` | `name`, `fn` | register/replace a handler at runtime |
| `del_handler` | `name` | remove a handler (no-op if absent) |
| `route` | `pipe_id`, `msg` | forward `msg` to sub-pipe `pipe_id` (no-op if `pipe_id` falsy) |
| `run_subpipe` | `data`, `handlers?`, `init_seq?` | spawn a child pipe and register it |
| `_shutdown` | — | set the shutdown flag and cascade `_shutdown` to all sub-pipes |
| `_error` | `error` \| `exception`, `original_msg?` | build/forward the canonical error envelope |

**Error envelope.** `_error` is both producer and consumer of one canonical shape:

```python
{"pipe_id": <originating pipe id>, "message": <str>, "original": <the msg that failed>}
```

A raw cause (string/exception) is wrapped into this shape; an already-structured
envelope is passed through unchanged. Errors bubble **up** to the parent; at a
root (no parent) an error triggers `_shutdown`. `pipe_id` preserves the *origin*
across hops, which is what you want for debugging.

---

## Hierarchy

```python
# spawn a child by sending run_subpipe to a running parent
await send(parent, {
    "type": "run_subpipe",
    "data": {"id": "worker"},
    "handlers": {"work": work_handler},
})

# then route messages down to it
await send(parent, {"type": "route", "pipe_id": "worker",
                    "msg": {"type": "work", "payload": 42}})
```

- `send(child, msg, dst="parent")` sends **up**.
- `send(parent, msg, dst="<child-id>")` (or a `route` message) sends **down**.
- `_shutdown` cascades through the whole subtree.

If `run_subpipe` is sent without `data` / without an `"id"`, the child registers
with `id=None` and still spawns — a missing id degrades gracefully rather than
crashing the parent.

---

## Utilities (`utils.py`)

| Function | Purpose |
|---|---|
| `load_handler_from_file(src_path, func_name)` | import a handler from a `.py` file; sync functions are auto-wrapped as async |
| `create_pipe_from_spec(spec, extra_data=None, *, parent_pipe=None)` | build a pipe from a declarative dict spec (handlers given as `{"src", "f_name"}`) |
| `async schedule(pipe, msg)` | fire-and-forget delayed send: `{"delay": <s>, "msg": {...}, "dst": ...}` |
| `async dispatch_debug(pipe, msg)` / `run_pipe_debug(pipe)` | drop-in debug variants that log type, timing, and whether `data` changed |

**Spec example:**

```python
spec = {
    "data": {"id": "p"},
    "handlers": {"work": {"src": "handlers/work.py", "f_name": "work"}},
    "init_seq": [{"type": "work"}],
}
pipe = create_pipe_from_spec(spec, extra_data={"role": "worker"})
```

---

## HTTP server (`server.py`)

A FastAPI app that proxies remote clients into the pipe tree. A root pipe runs
for the app lifetime; each `remote_id` gets a lazily-created **proxy pipe** under
the root, with an idle timeout.

```bash
uvicorn server:app
```

### `POST /pipe/{remote_id}`

Body is a JSON message.

- **`{"type": "ping"}`** → drains one queued outbound message:
  `{"msg": {...}}` or `{"msg": null}` if none.
- **any other message** → forwarded up toward the root; returns `{"status": "ok"}`.
- Optional **`"_timeout"`** (seconds) overrides the idle cleanup window
  (`DEFAULT_TIMEOUT = 30`). Each request resets the timer; once it elapses the
  proxy is shut down and de-registered.

Proxy creation is guarded by an `asyncio.Lock`, so concurrent first-requests for
the same `remote_id` create exactly one proxy (no TOCTOU race).

> Scope note: the proxy registry is in-memory with no auth/persistence — suitable
> for prototypes and low-throughput use, not as-is for production.

---

## Project layout

```
core.py     # runtime: pipes, send, dispatch, run_pipe, built-in handlers
utils.py    # dynamic handler loading, spec factory, schedule, debug loop
server.py   # FastAPI HTTP proxy over the pipe tree
template.py # Example
README.md   # doc
```

---

## Design notes & limitations

- **Dicts over classes.** At this size, dict-as-protocol keeps the runtime tiny
  and inspectable; the `Pipe` `TypedDict` documents the shape without inflating it.
- **Cancellation propagates.** `dispatch` only catches `Exception`; `CancelledError`
  / `KeyboardInterrupt` exit the loop as intended.
- **Unbounded queues.** `asyncio.Queue()` has no `maxsize` — fine for normal use,
  but there's no backpressure under sustained overload.
- **Send ownership.** After `send`, treat the message as consumed (fire-and-forget);
  the caller's original dict is left untouched.
