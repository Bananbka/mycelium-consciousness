import glob
import re
import sys

import yaml


def norm(v):
    if isinstance(v, dict):
        return {k: norm(x) for k, x in v.items()}
    if isinstance(v, list):
        return [norm(x) for x in v]
    if isinstance(v, str):
        v = (
            v.replace("clone-factory-", "")
            .replace("clone-config", "config")
            .replace("clone-secret", "secret")
        )
        return re.sub(r"^migrate-\d+$", "migrate", v)
    return v


def strip(o):
    o = norm(o)
    m = o.get("metadata", {})
    for k in ("namespace", "labels", "annotations"):
        m.pop(k, None)
    spec = o.get("spec", {})
    t = spec.get("template", {}).get("metadata")
    if t is not None:
        t.pop("labels", None)
        t.pop("annotations", None)
        if not t:
            spec["template"].pop("metadata")
    if o.get("kind") in ("Deployment", "Service"):
        spec.pop("selector", None)
    return o


def key(o):
    return (o["kind"], o["metadata"]["name"])


def flat(o, p=""):
    if isinstance(o, dict):
        for k, v in o.items():
            yield from flat(v, f"{p}.{k}")
    elif isinstance(o, list):
        for i, v in enumerate(o):
            yield from flat(v, f"{p}[{i}]")
    else:
        yield p, o


lab3 = {}
for f in sorted(glob.glob("k8s/*.yaml")):
    for d in yaml.safe_load_all(open(f, encoding="utf-8")):
        if d and d["kind"] != "Namespace":
            s = strip(d)
            lab3[key(s)] = s

chart = {}
for d in yaml.safe_load_all(open(sys.argv[1], encoding="utf-8-sig")):
    if not d:
        continue
    n = d["metadata"]["name"]
    if "preupgrade-check" in n or "test-api" in n:
        continue
    s = strip(d)
    chart[key(s)] = s

print(
    f"lab3 objects (no Namespace): {len(lab3)}   chart objects (no hooks): {len(chart)}"
)
print("only in lab3:", sorted(set(lab3) - set(chart)))
print("only in chart:", sorted(set(chart) - set(lab3)))
same = diff = 0
for k in sorted(set(lab3) & set(chart)):
    a, b = dict(flat(lab3[k])), dict(flat(chart[k]))
    ds = [
        (p, a.get(p), b.get(p)) for p in sorted(set(a) | set(b)) if a.get(p) != b.get(p)
    ]
    if not ds:
        same += 1
        continue
    diff += 1
    print(f"\n{k[0]}/{k[1]}")
    for p, x, y in ds:
        print(f"   {p}: lab3={x!r}  chart={y!r}")
print(f"\nidentical after normalisation: {same}, with differences: {diff}")
