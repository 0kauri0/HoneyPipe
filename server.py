import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from core import create_pipe, send, run_pipe


@asynccontextmanager
async def lifespan(app):
    asyncio.create_task(run_pipe(root))
    yield

app = FastAPI(lifespan=lifespan)
root = create_pipe(data={"id": "root"})
proxies = {}
_create_lock = asyncio.Lock()

DEFAULT_TIMEOUT = 30


async def remove_proxy(remote_id: str):
    proxy = proxies.pop(remote_id, None)
    if proxy:
        await send(proxy["pipe"], {"type": "_shutdown"})
        root["sub_pipes"] = [s for s in root["sub_pipes"] if s["id"] != remote_id]


@app.post("/pipe/{remote_id}")
async def pipe_endpoint(remote_id: str, payload: dict):
    async with _create_lock:
        if remote_id not in proxies:
            outgoing = asyncio.Queue()

            async def to_remote(pipe, msg):
                inner = msg.get("msg")
                if inner is not None:
                    await outgoing.put(inner)

            proxy = create_pipe(
                data={"id": remote_id, "outgoing": outgoing},
                parent={"id": root["data"]["id"], "queue": root["queue"]},
                handlers={"to_remote": to_remote},
            )
            root.setdefault("sub_pipes", []).append(
                {"id": remote_id, "queue": proxy["queue"]}
            )
            asyncio.create_task(run_pipe(proxy))
            proxies[remote_id] = {"pipe": proxy, "timer": None}

    proxy_entry = proxies[remote_id]
    proxy = proxy_entry["pipe"]

    if proxy_entry["timer"]:
        proxy_entry["timer"].cancel()

    timeout = payload.get("_timeout", DEFAULT_TIMEOUT)

    async def _wait_and_cleanup():
        try:
            await asyncio.sleep(timeout)
        except asyncio.CancelledError:
            return
        await remove_proxy(remote_id)

    proxy_entry["timer"] = asyncio.create_task(_wait_and_cleanup())

    if payload.get("type") == "ping":
        try:
            msg = proxy["data"]["outgoing"].get_nowait()
            return {"msg": msg}
        except asyncio.QueueEmpty:
            return {"msg": None}
    else:
        await send(proxy, payload, dst="parent")
        return {"status": "ok"}
