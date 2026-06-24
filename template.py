import asyncio

from core import create_pipe, run_pipe, send
from utils import run_pipe_debug, schedule


# -- root handlers --
async def start(pipe, msg):
    await send(
        pipe,
        {
            "type": "run_subpipe",
            "data": {"id": "sub"},
            "handlers": {"work": work, "tick": tick},
            "init_seq": [{"type": "work"}],
        },
    )


async def report(pipe, msg):
    print(f"[root] from {msg['src']}: {msg['data']}")


# -- sub handlers --
async def work(pipe, msg):
    print("[sub] work")
    await schedule(pipe, {"delay": 0.5, "msg": {"type": "tick"}, "dst": "self"})


async def tick(pipe, msg):
    print("[sub] tick -> parent")
    await send(pipe, {"type": "report", "data": "ok"}, dst="parent")


async def main():
    root = create_pipe(data={"id": "root"}, handlers={"start": start, "report": report})
    bg = asyncio.create_task(run_pipe_debug(root))
    await send(root, {"type": "start"})
    await asyncio.sleep(1)
    await send(root, {"type": "_shutdown"})
    await bg


if __name__ == "__main__":
    asyncio.run(main())
