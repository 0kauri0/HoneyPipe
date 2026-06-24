import asyncio
import uuid
from typing import TypedDict
from collections.abc import Callable


class Pipe(TypedDict, total=False):
    """Shape of a pipe. Makes the implicit protocol explicit (Clue 7)."""
    data: dict
    parent: dict
    queue: asyncio.Queue
    sub_pipes: list
    handlers: dict[str, Callable]


async def send(pipe, msg, dst=None):
    # Copy the caller's dict instead of mutating it (Clue 4): send() owns the
    # outgoing message, but the caller's `msg` must remain reusable.
    msg = {**msg, "id": str(uuid.uuid4())[:8], "src": pipe["data"]["id"]}

    if dst is None or dst == "self":
        target = pipe["queue"]
    elif dst == "parent":
        target = pipe.get("parent", {}).get("queue")
        if target is None:
            raise RuntimeError("No parent queue")
    else:
        for sub in pipe.get("sub_pipes", []):
            if sub["id"] == dst:
                target = sub["queue"]
                break
        else:
            raise ValueError(f"Subpipe '{dst}' not found")
    await target.put(msg)


async def add_handler(pipe, msg):
    pipe["handlers"][msg["name"]] = msg["fn"]

async def del_handler(pipe, msg):
    pipe["handlers"].pop(msg["name"], None)

async def _shutdown(pipe, msg):
    pipe["data"]["_shutdown"] = True
    for sub in pipe.get("sub_pipes", []):
        await send(pipe, {"type": "_shutdown"}, dst=sub["id"])

async def _error(pipe, msg):
    # Canonical error envelope (Clue 1): one shape, same keys at every hop.
    #   {"pipe_id": str, "message": str, "original": dict}
    # `_error` is both producer (wraps a raw exception) and consumer (forwards
    # an already-structured envelope up the tree).
    raw = msg.get("error")
    if isinstance(raw, dict):
        err = raw  # already structured — pass through unchanged
    else:
        raw_cause = raw if raw is not None else msg.get("exception", "")
        err = {
            "pipe_id": pipe["data"].get("id", "?"),
            "message": str(raw_cause),
            "original": msg.get("original_msg", {}),
        }
    if pipe.get("parent", {}).get("queue"):
        await send(pipe, {"type": "_error", "error": err}, dst="parent")
    else:
        await send(pipe, {"type": "_shutdown"})

async def route(pipe, msg):
    target_id = msg.get("pipe_id")
    if target_id:
        await send(pipe, msg.get("msg", {}), dst=target_id)

async def run_subpipe(pipe, msg):
    child = create_pipe(
        data=msg.get("data", {}),
        parent={"id": pipe["data"]["id"], "queue": pipe["queue"]},
        handlers=msg.get("handlers", {}),
        init_seq=msg.get("init_seq", [])
    )
    pipe.setdefault("sub_pipes", []).append({"id": child["data"].get("id"), "queue": child["queue"]})
    asyncio.create_task(run_pipe(child))


def create_pipe(data=None, parent=None, handlers=None, init_seq=None):
    pipe = {
        "data": data or {},
        "parent": parent or {},
        "queue": asyncio.Queue(),
        "sub_pipes": [],
        "handlers": {
            "add_handler": add_handler,
            "del_handler": del_handler,
            "_shutdown": _shutdown,
            "_error": _error,
            "route": route,
            "run_subpipe": run_subpipe,
        }
    }
    if handlers:
        pipe["handlers"].update(handlers)
    for msg in init_seq or []:
        pipe["queue"].put_nowait(msg)
    return pipe


async def dispatch(pipe, msg):
    if "type" not in msg:
        await _error(pipe, {
            "exception": "Missing 'type' in message",
            "original_msg": msg,
        })
        return
    handler = pipe["handlers"].get(msg["type"])
    if not handler:
        await _error(pipe, {
            "exception": f"Unknown message type: {msg['type']}",
            "original_msg": msg,
        })
        return
    try:
        await handler(pipe, msg)
    except Exception as e:
        await _error(pipe, {"exception": e, "original_msg": msg})


async def run_pipe(pipe):
    while not pipe["data"].get("_shutdown"):
        msg = await pipe["queue"].get()
        await dispatch(pipe, msg)
