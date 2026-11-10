"""Contract versions, the migration gate, and the compatibility-view SQL."""
from __future__ import annotations

import copy

from .models import Change, Column, Contract, day_to_date


def apply_agreement(contract: Contract, agreement) -> Contract:
    """Return the next contract version with the agreed deprecation recorded."""
    new = copy.deepcopy(contract)
    new.version += 1
    ch = agreement.change
    keep_until = agreement.effective_day + max(agreement.overlap_days,
                                               agreement.view_days if agreement.compat_view else 0)
    old = new.columns[ch.column]
    if ch.kind == "rename_column":
        new.columns[ch.new_name] = Column(ch.new_name, old.dtype)
        old.deprecated_until, old.replaced_by = keep_until, ch.new_name
        new.notes.append(f"v{new.version}: `{ch.column}` renamed to `{ch.new_name}`, "
                         f"old name readable until {day_to_date(keep_until)}")
    elif ch.kind == "drop_column":
        old.deprecated_until = keep_until
        new.notes.append(f"v{new.version}: `{ch.column}` dropped after {day_to_date(keep_until)}")
    elif ch.kind == "change_type":
        new.notes.append(f"v{new.version}: `{ch.column}` becomes {ch.new_dtype}; compatibility view "
                         f"keeps {old.dtype} until {day_to_date(keep_until)}")
        old.dtype = ch.new_dtype
    elif ch.kind == "change_meaning":
        new.notes.append(f"v{new.version}: `{ch.column}` values are now multiplied by {ch.factor:g}; "
                         f"compatibility view keeps the old meaning until {day_to_date(keep_until)}")
    return new


def check_change(contract: Contract, change: Change, today: int):
    """Gate a migration against the active contract. Returns (allowed, reasons).

    A migration job must call this before running. A breaking change only passes
    once an agreed deprecation window has ended."""
    col = contract.columns.get(change.column)
    if col is None:
        return False, [f"Column `{change.column}` is not part of contract v{contract.version}."]
    if col.deprecated_until is None:
        return False, [f"BLOCKED: `{change.column}` is guaranteed by contract v{contract.version} "
                       f"and has no agreed deprecation."]
    if change.effective_day < col.deprecated_until:
        return False, [f"BLOCKED: `{change.column}` is guaranteed until {day_to_date(col.deprecated_until)}; "
                       f"the change is scheduled for {day_to_date(change.effective_day)}."]
    return True, ["Deprecation window ended. Change allowed."]


def compat_view_sql(table: str, change: Change, old_dtype: str = "string") -> str:
    """SQL for the compatibility view that keeps the old shape alive."""
    if change.kind == "rename_column":
        return (f"CREATE OR REPLACE VIEW {table}_v1 AS\n"
                f"SELECT *, {change.new_name} AS {change.column} FROM {table};")
    if change.kind == "change_type":
        return (f"CREATE OR REPLACE VIEW {table}_v1 AS\n"
                f"SELECT * EXCEPT ({change.column}), CAST({change.column} AS {old_dtype}) AS {change.column} "
                f"FROM {table};")
    if change.kind == "change_meaning":
        return (f"CREATE OR REPLACE VIEW {table}_v1 AS\n"
                f"SELECT * EXCEPT ({change.column}), {change.column} / {change.factor:g} AS {change.column} "
                f"FROM {table};")
    return "-- no compatibility view for this kind of change"
