"""Turn measured consumers into negotiating agents.

A team's needs cannot be observed from the data, so they are explicit inputs. Defaults are derived
from two measurable facts: how often the consumer refreshes and how many reports are built on it.
Any default can be overridden (the app lets you edit them).
"""
from __future__ import annotations

import difflib
import math

from .backend import category
from .models import Consumer

# kinds that read through SQL can use a compatibility view; reports and models can use a frozen snapshot
VIEW_KINDS = ("dashboard", "report", "stream_job")
SNAPSHOT_KINDS = ("report", "ml_model")


def defaults(c: dict) -> dict:
    """c: a consumer record from the snapshot."""
    cycle_days = c["cadence_hours"] / 24
    chain = 7 * c["depth"]  # every layer of reports built on top needs another week
    return {"notice_days": min(30, max(1, math.ceil(2 * cycle_days)) + chain),
            "min_overlap_days": min(60, max(1, math.ceil(3 * cycle_days)) + chain),
            "freeze": None, "can_use_view": c["kind"] in VIEW_KINDS,
            "can_use_snapshot": c["kind"] in SNAPSHOT_KINDS}


def has_replacement(cols: list, column: str) -> bool:
    """Is there another column of the same kind with a similar name? (cheap heuristic)"""
    types = dict(cols)
    cat = category(types[column])
    return any(n != column and category(t) == cat and
               difflib.SequenceMatcher(None, n, column).ratio() >= 0.6 for n, t in cols)


def build_consumer(c: dict, overrides: dict | None = None, no_replacement: frozenset = frozenset()) -> Consumer:
    d = {**defaults(c), **(overrides or {})}
    return Consumer(name=c["name"], kind=c["kind"], owner=c["owner"], notice_days=int(d["notice_days"]),
                    min_overlap_days=int(d["min_overlap_days"]), freeze=d["freeze"],
                    can_use_view=bool(d["can_use_view"]), can_use_snapshot=bool(d["can_use_snapshot"]),
                    hard_dependency=set(no_replacement))


KIND_OF = {"rename_column": "rename", "drop_column": "drop", "change_type": "retype", "change_meaning": "meaning"}


def affected(snapshot: dict, table: str, change) -> list:
    """Consumers whose results broke or silently changed in the measurement, with the evidence."""
    kind = KIND_OF[change.kind]
    return [r for r in snapshot["impact"]
            if (r["table"], r["column"], r["kind"], r["mode"]) == (table, change.column, kind, "no_contract")
            and (kind != "retype" or r["param"] == change.new_dtype) and r["outcome"] != "ok"]


def with_contract_rows(snapshot: dict, table: str, change) -> dict:
    kind = KIND_OF[change.kind]
    return {r["consumer"]: r for r in snapshot["impact"]
            if (r["table"], r["column"], r["kind"], r["mode"]) == (table, change.column, kind, "with_contract")
            and (kind != "retype" or r["param"] == change.new_dtype)}
