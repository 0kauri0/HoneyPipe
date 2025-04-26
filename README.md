# HoneyPipe
## 🍯 HoneyPipe — the “sweet & simple” dict-pipeline micro-framework  

Send your data along a chain of tiny *steps* that *bzzz* and let each function
add its own drop of nectar.  
With just one operator `>>` you can mutate a shared dict in-place, mix sync
and async code, and orchestrate whole *hives* without boiler-plate.

```
dict_source  >>  step1  >>  step2  >>  step3
```

• **Honey** = the key/value payload you care about.  
• **Pipe**   = the right-shift chain that carries it.  
• **Bees**   = your individual functions / methods working in parallel hives.

---

### 0. 30-second mental model  

```
┌─────────┐        >>      ┌─────────────┐   >>   ┌─────────────┐
│  dict   │ ────▶ 🐝 step │  🐝 step    │ ───▶  │   🐝 step   │
└─────────┘                 (adds nectar)         (adds nectar …)
```

* A **Step** is “a callable that accepts a dict, mutates it, returns the *same* dict”.  
* `data >> step1 >> step2` works because `Step.__rrshift__` and `Step.__rshift__`
  handle the plumbing for you.  
* Decorators `conditional_proc` / `conditional_func` (and, if you need raw
  control, `_condition`) transform regular Python callables into these Step
  objects—instantly chainable, no wrappers to write.

---

### 1.  The four drops of nectar you’ll use most  

| # | Name | Decorates | Returns | Use case |
|---|------|-----------|---------|----------|
|1|`_condition(pred)`|sync/async **function(d)**|plain callable|quick yes/no gate, manual call or `Step(...)` wrap|
|2|`conditional_proc(pred)`|sync/async **procedure(d)**|`Step`|in-place mutation, perfect for business logic|
|3|`conditional_func(pred, store_return_as="key")`|sync/async **func(x,y, …)**|`Step`|pulls args from dict, stores computed honey back|
|4|`Step`|n/a|n/a|glues everything together with `>>`|


---

### 2.  End-to-end demo (`demo.py`)  

```python
import asyncio, random
from honeypipe import Step, _condition, conditional_proc, conditional_func

# ───────────────────── 1) plain function → Step via conditional_proc
@conditional_proc(lambda d: "x" not in d)   # fires once
def add_x(d): d["x"] = 1

# ───────────────────── 2) async function → Step via conditional_proc
@conditional_proc(lambda d: d.get("want_async"))
async def async_double_a(d):
    await asyncio.sleep(0.05)
    d["a"] *= 2

# ───────────────────── 3) value-producing func → Step via conditional_func
@conditional_func(lambda d: d["b"] > 0, store_return_as="hyp")
def hyp(a: int, b: int):                   # pulls a & b from *dict*!
    return (a*a + b*b) ** 0.5

# ───────────────────── 4) method steps inside a custom dict subclass
class Data(dict):
    @staticmethod
    @conditional_proc(lambda d: True)
    def tag(d): d["tagged"] = True

    @staticmethod
    @conditional_func(store_return_as="half")
    def half(a): return a / 2

# ───────────────────── 5) optional “verbose” printer via _condition + Step
@_condition(lambda d: d.get("verbose"))
def debug_printer(d): print("STATE:", d)

Debug = Step(debug_printer)    # wrap manually ⇒ chainable

# ───────────────────── 6) generator that yields dicts  (“sources”)
def gen():
    for i in range(3):
        yield Data(a=i, b=i+1, want_async=bool(i % 2), verbose=True)

# ───────────────────── 7) build a *composite* pipeline once
PIPE = add_x >> async_double_a >> hyp >> Data.tag >> Data.half >> Debug

# ───────────────────── 8) run!
async def main():
    for d in gen():           # any dict-like “source”
        await (d >> PIPE)     # leftmost await handles async links
        print("FINAL ⇒", {k: d[k] for k in sorted(d)})

asyncio.run(main())
```

Example output
```
STATE: {'a': 0, 'b': 1, 'half': 0.0, 'hyp': 1.0, 'tagged': True, 'x': 1, 'verbose': True, 'want_async': False}
FINAL ⇒ {'a': 0, 'b': 1, 'half': 0.0, 'hyp': 1.0, 'tagged': True, 'verbose': True, 'want_async': False, 'x': 1}
STATE: {'a': 2, 'b': 2, 'half': 1.0, 'hyp': 2.8284271247461903, 'tagged': True, 'x': 1, 'verbose': True, 'want_async': True}
FINAL ⇒ {'a': 2, 'b': 2, 'half': 1.0, 'hyp': 2.8284271247461903, 'tagged': True, 'verbose': True, 'want_async': True, 'x': 1}
STATE: {'a': 2, 'b': 3, 'half': 1.5, 'hyp': 3.605551275463989, 'tagged': True, 'x': 1, 'verbose': True, 'want_async': False}
FINAL ⇒ {'a': 2, 'b': 3, 'half': 1.5, 'hyp': 3.605551275463989, 'tagged': True, 'verbose': True, 'want_async': False, 'x': 1}
```

---

### 3.  Micro-recipes  

#### 3.1  Turn an existing `_condition`-decorated function into a chainable step  

```python
@_condition(lambda d: d.get("mode") == "debug")
def log(d): print(d)

LogStep = Step(log)
data >> LogStep >> other_step
```

#### 3.2  Compose a “mega” loop step  

```python
@conditional_proc(lambda d: d["next"] != "exit")
def agent_cycle(d):
    while d["next"] != "exit":
        d["tick"] = d.get("tick", 0) + 1
        if d["tick"] > 3: d["next"] = "exit"

data >> agent_cycle >> after_cycle
```

#### 3.3  Attach steps from different files to one class  

```python
# file a.py
from honeypipe import conditional_proc, Step
from domain import Actor             # same class everywhere

@staticmethod
@conditional_proc(lambda d: True)
def preprocess(d): d["p"] = 1

Actor.preprocess = preprocess        # plug-in

# file main.py
data >> Actor.preprocess >> Actor.other_step
```

---

### 4.  Cheat-sheet  

```
def f(d): ...              # plain             ──► Step(f) if you need >>
@_condition(pred)          # gated call        ──► Step(...) if you need >>
@conditional_proc(pred)    # gated *procedure* ──► ready for >>
@conditional_func(pred, store_return_as="k")
                           # gated *function*  ──► ready for >>
```

All three roads end in a Step object; once you have one, you can snap it into the `>>` pipeline like a hex-cell in a honeycomb.
