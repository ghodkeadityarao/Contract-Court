"""Generate a workload of downstream reports from the SCHEMA alone (no hand-picked consumers).

Real organisations already have consumers. We have none, so this builds a deterministic set of
dashboards, features, reports and monitors over whatever tables exist, including reports built on
other reports. Everything about a consumer except its SQL (kind, owner, refresh cadence) is
assigned by template and is stored as data, not code.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .backend import category

SAFE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
KIND_META = {  # template role -> (owner team, refresh cadence in hours)
    "dashboard": ("BI team", 24), "ml_model": ("Data science", 168),
    "report": ("Finance and operations", 168), "stream_job": ("Risk", 1)}
TEXTY = ("email", "name", "text", "message", "description", "url", "comment", "log", "address", "phone")


@dataclass
class Spec:
    name: str
    sql: str
    kind: str
    owner: str
    cadence_hours: int
    level: int
    outputs: tuple = ()  # output column names (used to build level-2 reports)


def _singular(t: str) -> str:
    return t[:-3] + "y" if t.endswith("ies") else t[:-1] if t.endswith("s") else t


def _spec(name, sql, kind, level=1, outputs=()):
    owner, cadence = KIND_META[kind]
    return Spec(name, sql, kind, owner, cadence, level, tuple(outputs))


def _roles(cols):
    cols = [(n, t) for n, t in cols if SAFE.match(n)]
    ids = [n for n, _ in cols if n == "id" or n.endswith("_id")]
    nums = [n for n, t in cols if category(t) == "numeric" and n not in ids]
    times = [n for n, t in cols if category(t) == "temporal"]
    cats = [n for n, t in cols if category(t) == "string" and n not in ids
            and not any(k in n for k in TEXTY)]
    return ids, nums, times, cats


def _candidates(table, cols):
    ids, nums, times, cats = _roles(cols)
    out = []
    for cat in cats[:2]:
        extra = f", SUM({nums[0]}) AS total_{nums[0]}" if nums else ""
        out.append(_spec(f"agg_{table}_by_{cat}",
                         f"SELECT {cat}, COUNT(*) AS n{extra} FROM {table} GROUP BY {cat} ORDER BY {cat} LIMIT 200",
                         "dashboard", outputs=[cat, "n"]))
    if times and nums:
        out.append(_spec(f"trend_{table}_{nums[0]}",
                         f"SELECT substr(CAST({times[0]} AS STRING),1,7) AS month, SUM({nums[0]}) AS total "
                         f"FROM {table} GROUP BY 1 ORDER BY 1", "dashboard"))
    for fk in [i for i in ids if i != "id"][:2]:
        parts = [f"{fk}", "COUNT(*) AS n"]
        if nums:
            parts.append(f"AVG({nums[0]}) AS avg_{nums[0]}")
        if times:
            parts.append(f"MAX(CAST({times[0]} AS STRING)) AS last_seen")
        out.append(_spec(f"features_{table}_by_{fk}",
                         f"SELECT {', '.join(parts)} FROM {table} GROUP BY {fk} ORDER BY {fk} LIMIT 200", "ml_model"))
    latest = f"MAX(CAST({times[0]} AS STRING))" if times else (f"MAX({nums[0]})" if nums else "COUNT(*)")
    out.append(_spec(f"latest_{table}", f"SELECT COUNT(*) AS n, {latest} AS latest FROM {table}", "stream_job"))
    return out


def _joins(tables):
    out = []
    for a, acols in tables.items():
        a_ids, a_nums, _, _ = _roles(acols)
        if not a_nums:
            continue
        for fk in [i for i in a_ids if i != "id"]:
            for b, bcols in tables.items():
                if b == a or fk != _singular(b) + "_id":
                    continue
                b_ids, _, _, b_cats = _roles(bcols)
                pk = fk if fk in b_ids else "id" if "id" in b_ids else None
                if pk and b_cats:
                    out.append(_spec(
                        f"join_{a}_{b}",
                        f"SELECT {b}.{b_cats[0]} AS {b_cats[0]}, COUNT(*) AS n, SUM({a}.{a_nums[0]}) AS total "
                        f"FROM {a} JOIN {b} ON {a}.{fk} = {b}.{pk} GROUP BY {b}.{b_cats[0]} "
                        f"ORDER BY {b}.{b_cats[0]} LIMIT 200", "report"))
    return out


def generate(tables: dict, max_consumers: int = 30) -> list:
    """tables: name -> [(column, type)]. Returns specs, level-1 first, round-robin across tables."""
    per_table = [_candidates(t, c) for t, c in tables.items()]
    picked, i = [], 0
    pool = [s for lst in per_table for s in lst]  # for stable ordering of joins after
    while len(picked) < max_consumers * 3 // 4 and any(per_table):
        lst = per_table[i % len(per_table)]
        if lst:
            picked.append(lst.pop(0))
        i += 1
    picked += _joins(tables)[: max(1, max_consumers // 6)]
    level1 = picked[:max_consumers]
    level2 = []
    for s in level1:
        if len(level1) + len(level2) >= max_consumers + max_consumers // 5:
            break
        if s.level == 1 and s.kind == "dashboard" and len(s.outputs) >= 2:
            level2.append(_spec(f"top_{s.name}", f"SELECT {s.outputs[0]}, n FROM {s.name} ORDER BY n DESC LIMIT 10",
                                "report", level=2, outputs=s.outputs[:1] + ("n",)))
    return level1 + level2[: max(1, max_consumers // 5)]
