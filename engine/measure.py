"""Measure the real blast radius of every possible change by running the consumers' own SQL.

Sandbox layout: each base table T is a VIEW over a physical copy T__orig. To test a change we
redefine the view T (metadata only, cheap), re-run every consumer that depends on T, and compare
with the baseline. Chains of reports are handled for free because they are real views.
"""
from __future__ import annotations

import re
from decimal import Decimal

from .backend import category

TARGET_TYPES = {"numeric": ["string", "bigint", "double"], "string": ["bigint", "double"],
                "temporal": ["string"], "bool": ["string", "bigint"], "other": []}


# ------------------------------------------------------------------- sandbox
def _select(cols, kind=None, column=None, param=None, compat=False):
    out = []
    for name, _ in cols:
        if name != column:
            out.append(name)
        elif kind == "drop":
            continue
        elif kind == "rename":
            out += ([name] if compat else []) + [f"{name} AS {param}"]
        elif kind == "retype":
            expr = f"TRY_CAST({name} AS {param})"
            out += [name, f"{expr} AS {name}__new"] if compat else [f"{expr} AS {name}"]
        elif kind == "meaning":
            expr = f"{name} * {param}"
            out += [name, f"{expr} AS {name}__v2"] if compat else [f"{expr} AS {name}"]
    return ", ".join(out)


def _set_view(backend, table, cols, **variant):
    backend.exec(f"CREATE OR REPLACE VIEW {table} AS SELECT {_select(cols, **variant)} FROM {table}__orig")


def build_sandbox(backend, tables: dict, specs: list) -> None:
    """Expects physical tables `<T>__orig`. Creates view T and every consumer view."""
    for t, cols in tables.items():
        _set_view(backend, t, cols)
    for s in sorted(specs, key=lambda s: s.level):
        backend.exec(f"CREATE OR REPLACE VIEW {s.name} AS {s.sql}")


# ------------------------------------------------------------- dependencies
def dependencies(specs: list, tables: dict) -> dict:
    """name -> {base_tables (transitive), depth (longest chain of reports built on it)}"""
    names = set(tables) | {s.name for s in specs}
    direct = {s.name: {n for n in names if n != s.name and re.search(rf"\b{re.escape(n)}\b", s.sql)}
              for s in specs}

    def base(n, seen=()):
        if n in tables:
            return {n}
        return set().union(*[base(d, seen + (n,)) for d in direct.get(n, ()) if d not in seen] or [set()])

    def depth(n):
        kids = [s.name for s in specs if n in direct[s.name]]
        return 0 if not kids else 1 + max(depth(k) for k in kids)

    return {s.name: {"base_tables": sorted(base(s.name)), "reads": sorted(direct[s.name]),
                     "depth": depth(s.name)} for s in specs}


# ---------------------------------------------------------------- comparison
def _norm_value(v):
    if isinstance(v, (float, Decimal)):
        return round(float(v), 4)
    return None if v is None else str(v)


def _snapshot(rows) -> list:
    return sorted((tuple(_norm_value(v) for v in r) for r in rows), key=lambda r: tuple(map(str, r)))


def _run_consumers(backend, names: list, baseline: dict) -> dict:
    out = {}
    for n in names:
        try:
            same = _snapshot(backend.run(f"SELECT * FROM {n}")) == baseline[n]
            out[n] = ("ok", "same results as before") if same else \
                ("silent", "runs, but silently returns different numbers")
        except Exception as e:  # noqa: BLE001 - the raw failure is the evidence
            out[n] = ("error", str(e).strip().split("\n")[0][:160])
    return out


def canary(backend, tables: dict, specs: list):
    """Sanity check that the sandbox really reacts to a change: drop a column a consumer reads and
    confirm that consumer fails. Returns (consumer, table, column, broke) or None."""
    deps = dependencies(specs, tables)
    for s in specs:
        for t in deps[s.name]["base_tables"]:
            for col, _ in tables[t]:
                if re.search(rf"\b{re.escape(col)}\b", s.sql):
                    _set_view(backend, t, tables[t], kind="drop", column=col, param="", compat=False)
                    try:
                        backend.run(f"SELECT * FROM {s.name}")
                        broke = False
                    except Exception:  # noqa: BLE001
                        broke = True
                    finally:
                        _set_view(backend, t, tables[t])
                    return s.name, t, col, broke
    return None


# ------------------------------------------------------------------ measure
def measure(backend, tables: dict, specs: list, factor: float = 100.0, progress=print) -> dict:
    """Returns {"rows": [...], "unread": [[table, column], ...], "deps": {...}}."""
    deps = dependencies(specs, tables)
    baseline = {s.name: _snapshot(backend.run(f"SELECT * FROM {s.name}")) for s in specs}
    rows, unread = [], []

    def trial(table, cols, kind, column, param, compat, only=None):
        names = only or [n for n, d in deps.items() if table in d["base_tables"]]
        try:
            _set_view(backend, table, cols, kind=kind, column=column, param=param, compat=compat)
        except Exception as e:  # noqa: BLE001 - this change is not expressible on this column: skip it
            progress(f"  skipped {table}.{column} {kind} {param}: {str(e).strip().splitlines()[0][:100]}")
            _set_view(backend, table, cols)
            return None
        try:
            return _run_consumers(backend, names, baseline)
        finally:
            _set_view(backend, table, cols)  # always restore

    def record(table, column, kind, param, mode, results):
        for consumer, (outcome, detail) in results.items():
            rows.append({"table": table, "column": column, "kind": kind, "param": param, "mode": mode,
                         "consumer": consumer, "outcome": outcome, "detail": detail})

    for table, cols in tables.items():
        progress(f"measuring {table} ({len(cols)} columns)")
        for column, typ in cols:
            res = trial(table, cols, "drop", column, "", False)
            if res is None or all(o == "ok" for o, _ in res.values()):
                unread.append([table, column])
                continue
            record(table, column, "drop", "", "no_contract", res)
            variants = [("rename", f"{column}_renamed")]
            variants += [("retype", t) for t in TARGET_TYPES[category(typ)]]
            if category(typ) == "numeric":
                variants.append(("meaning", f"{factor:g}"))
            for kind, param in variants:
                res = trial(table, cols, kind, column, param, False)
                if res is None:
                    continue
                record(table, column, kind, param, "no_contract", res)
                hit = [n for n, (o, _) in res.items() if o != "ok"]
                if hit:  # does the "keep the old shape next to the new one" plan really work?
                    wc = trial(table, cols, kind, column, param, True, only=hit)
                    if wc is not None:
                        record(table, column, kind, param, "with_contract", wc)
    return {"rows": rows, "unread": unread, "deps": deps}


def build_snapshot(source: str, tables: dict, specs: list, result: dict, sample_rows: int,
                   uc_lineage=None) -> dict:
    return {
        "source": source, "sample_rows": sample_rows,
        "tables": {t: [list(c) for c in cols] for t, cols in tables.items()},
        "consumers": [{"name": s.name, "kind": s.kind, "owner": s.owner, "cadence_hours": s.cadence_hours,
                       "level": s.level, "sql": s.sql, **result["deps"][s.name]} for s in specs],
        "impact": result["rows"], "unread": result["unread"], "uc_lineage": uc_lineage or [],
    }
