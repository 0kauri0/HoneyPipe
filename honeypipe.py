"""
HoneyPipe — core building blocks
--------------------------------
Four helpers that let you write

    data >> step_a >> step_b >> step_c

where each *step* is just a function (sync or async) that mutates the dict.
"""

import asyncio, functools, inspect
from typing import Callable, Dict, Any   # ←- type aliases used below
                                         # (kept optional; remove if you dislike mypy)

# ─────────────────────────────────────────────────────────────────────────────
# 1.  Low-level gate: @_condition
#     • Returns a *regular* callable, NOT a Step.
#     • Wrap manually in Step(...) if you want to chain it with >>.
# ─────────────────────────────────────────────────────────────────────────────
def _condition(predicate: Callable[[Dict[str, Any]], bool]):
    """
    Quick yes/no gate for an existing function(d).

        @_condition(lambda d: d['debug'])
        def printer(d): ...

    If the predicate is false, the wrapper simply returns None.
    """
    def decorator(func):
        if asyncio.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(d):
                if predicate(d):
                    return await func(d)
            return async_wrapper
        else:
            @functools.wraps(func)
            def wrapper(d):
                if predicate(d):
                    return func(d)
            return wrapper
    return decorator


# ─────────────────────────────────────────────────────────────────────────────
# 2.  Step class — provides the >> operator glue
# ─────────────────────────────────────────────────────────────────────────────
class Step:
    """Wrap a callable so that `data >> step` and `step1 >> step2` work."""
    def __init__(self, f: Callable[[Dict[str, Any]], None]):
        self.f = f

    # make the instance itself callable
    def __call__(self, d: Dict[str, Any]):
        self.f(d)            # run side-effect
        return d             # always forward the *same* dict

    # allow  data >> step
    def __rrshift__(self, d: Dict[str, Any]):
        return self(d)

    # allow  step1 >> step2
    def __rshift__(self, other):
        other = Step(other)   # idempotent: Step(Step(...)) is harmless
        return Step(lambda d: (self(d), other(d))[1])  # run both, keep dict


# ─────────────────────────────────────────────────────────────────────────────
# 3.  conditional_func — function that *produces* a value
#     • Pulls parameters from dict by name (func(**d_subset)).
#     • Optionally stores the return value back into the dict.
#     • Always returns a Step object.
# ─────────────────────────────────────────────────────────────────────────────
def conditional_func(
    predicate: Callable[[Dict[str, Any]], bool] = lambda d: True,
    *,
    store_return_as: str | None = None
):
    """
    Example:

        @conditional_func(lambda d: d['x'] > 0, store_return_as="hyp")
        def hyp(x, y): return (x**2 + y**2) ** 0.5
    """
    def decorate(func):
        sig = inspect.signature(func)
        needed = [p.name for p in sig.parameters.values()
                  if p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)]

        async def async_gate(d: Dict[str, Any]):
            if predicate(d):
                rv = await func(**{k: d[k] for k in needed if k in d})
                if store_return_as:
                    d[store_return_as] = rv

        def sync_gate(d: Dict[str, Any]):
            if predicate(d):
                rv = func(**{k: d[k] for k in needed if k in d})
                if store_return_as:
                    d[store_return_as] = rv

        gate = async_gate if asyncio.iscoroutinefunction(func) else sync_gate
        gate.__name__, gate.__doc__ = func.__name__, func.__doc__
        return Step(gate)          # ← ready for >>

    return decorate


# ─────────────────────────────────────────────────────────────────────────────
# 4.  conditional_proc — procedure that only mutates the dict
#     • Signature must be func(d) or async def func(d)
#     • Returns a Step object directly.
# ─────────────────────────────────────────────────────────────────────────────
def conditional_proc(predicate: Callable[[Dict[str, Any]], bool]):
    """
    Example:

        @conditional_proc(lambda d: d['debug'])
        def log(d): print(d)
    """
    def decorate(func: Callable):
        if asyncio.iscoroutinefunction(func):
            @functools.wraps(func)
            async def gated(d):
                if predicate(d):
                    await func(d)
        else:
            @functools.wraps(func)
            def gated(d):
                if predicate(d):
                    func(d)

        return Step(gated)

    return decorate
