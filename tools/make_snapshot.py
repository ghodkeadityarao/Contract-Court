"""Build tools/standin_snapshot.json from the LOCAL stand-in warehouse.

The real app/data/snapshot.json comes from the Databricks notebooks (deploy.sh fetches it). To try the app
on stand-in data, copy this file over app/data/snapshot.json yourself."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import standin  # noqa: E402
from engine import measure, workload  # noqa: E402
from engine.backend import DuckBackend  # noqa: E402


def build(con=None):
    con = con or standin.make()
    be = DuckBackend(con)
    names = [r[0] for r in con.execute("SHOW TABLES").fetchall() if r[0].endswith("__orig")]
    tables = {n[:-6]: be.columns(n) for n in sorted(names)}
    specs = workload.generate(tables)
    measure.build_sandbox(be, tables, specs)
    result = measure.measure(be, tables, specs, progress=lambda m: None)
    return measure.build_snapshot("local-standin", tables, specs, result, sample_rows=400)


if __name__ == "__main__":
    snap = build()
    out = os.path.join(HERE, "standin_snapshot.json")  # never overwrites app/data/snapshot.json
    json.dump(snap, open(out, "w"))
    print("wrote", out, "| consumers:", len(snap["consumers"]), "| impact rows:", len(snap["impact"]),
          "| unread columns:", len(snap["unread"]))
