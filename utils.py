import asyncio
import os
import time
import importlib.util
import sys
from pathlib import Path
from core import send, create_pipe

def smart_decode(data: bytes) -> str:
    """Decodes bytes using charset_normalizer with fallback to cp866."""
    from charset_normalizer import from_bytes
    if not data:
        return ""
    try:
        result = from_bytes(data).best()
        return str(result) if result else data.decode('cp866', errors='replace')
    except Exception:
        return data.decode('cp866', errors='replace')

async def start_process(cmd: str, cwd: str = None, env: dict = None):
    """Subprocess factory with standard environment sync."""
    _env = os.environ.copy()
    _env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
    if env:
        _env.update(env)

    return await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        stdin=asyncio.subprocess.PIPE,
        cwd=cwd,
        env=_env,
    )

def load_handler_from_file(src_path: str, func_name: str):
    path = Path(src_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Handler file not found: {path}")

    module_name = f"dyn_{abs(hash(str(path)))}_{func_name}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)

    handler = getattr(module, func_name)
    if not asyncio.iscoroutinefunction(handler):
        async def wrapper(*args, **kwargs):
            return handler(*args, **kwargs)
        return wrapper
    return handler

def create_pipe_from_spec(spec: dict, extra_data: dict = None, *, parent_pipe: dict = None):
    data = spec.get("data", {})
    if extra_data:
        data.update(extra_data)

    handlers = {}
    for name, hspec in spec.get("handlers", {}).items():
        if not (isinstance(hspec, dict) and "src" in hspec and "f_name" in hspec):
            raise ValueError(f"Handler '{name}': expected dict with src and f_name")
        handlers[name] = load_handler_from_file(hspec["src"], hspec["f_name"])

    parent = {"id": parent_pipe["data"]["id"], "queue": parent_pipe["queue"]} if parent_pipe else {}
    return create_pipe(data=data, parent=parent, handlers=handlers, init_seq=spec.get("init_seq"))

async def schedule(pipe, msg):
    # Support both dictionary-style and positional-style calls
    delay = msg.get("delay", 0)
    inner_msg = msg.get("msg")
    dst = msg.get("dst")
    
    async def delayed():
        await asyncio.sleep(delay)
        await send(pipe, inner_msg, dst=dst)
    asyncio.create_task(delayed())

async def dispatch_debug(pipe, msg):
    t0 = time.perf_counter()
    data_before = str(pipe["data"])
    print(f"[debug] >>> {msg.get('type')} pipe={pipe['data'].get('id')} msg={msg}")
    try:
        from core import dispatch
        await dispatch(pipe, msg)
    finally:
        t1 = time.perf_counter()
        data_after = str(pipe["data"])
        diff = "changed" if data_before != data_after else "no change"
        print(f"[debug] <<< {msg.get('type')} pipe={pipe['data'].get('id')} time={t1-t0:.4f}s {diff}")

async def run_pipe_debug(pipe):
    while not pipe["data"].get("_shutdown"):
        msg = await pipe["queue"].get()
        await dispatch_debug(pipe, msg)
