"""Data structures. Time is "days from today" (day 0 = BASE_DATE) so negotiations are deterministic."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Optional

BASE_DATE = dt.date(2026, 11, 9)


def day_to_date(day: int) -> str:
    return (BASE_DATE + dt.timedelta(days=day)).strftime("%b %d")


@dataclass
class Column:
    name: str
    dtype: str
    deprecated_until: Optional[int] = None  # day number; None = active
    replaced_by: Optional[str] = None


@dataclass
class Contract:
    table: str
    version: int
    columns: dict  # name -> Column
    notes: list = field(default_factory=list)


@dataclass
class Change:
    kind: str  # rename_column | drop_column | change_type | change_meaning
    column: str
    effective_day: int
    new_name: Optional[str] = None
    new_dtype: Optional[str] = None
    factor: Optional[float] = None  # change_meaning: values are multiplied by this

    def describe(self) -> str:
        if self.kind == "rename_column":
            return f"rename `{self.column}` to `{self.new_name}`"
        if self.kind == "drop_column":
            return f"drop `{self.column}`"
        if self.kind == "change_type":
            return f"change the type of `{self.column}` to {self.new_dtype}"
        return f"change the meaning of `{self.column}` (values now multiplied by {self.factor:g})"


@dataclass
class Proposal:
    change: Change
    effective_day: int
    overlap_days: int = 0  # how long the old column stays next to the new one
    compat_view: bool = False  # a view that keeps the old shape alive
    view_days: int = 0
    snapshot: bool = False  # frozen snapshot export of the old column

    def key(self):
        return (self.effective_day, self.overlap_days, self.compat_view, self.view_days, self.snapshot)


@dataclass
class Producer:
    name: str
    max_overlap_days: int  # longest it will keep two copies of a column
    max_view_days: int  # longest it will keep a compatibility view
    latest_day: int  # release deadline


@dataclass
class Consumer:
    name: str
    kind: str  # dashboard | ml_model | report | stream_job
    owner: str
    notice_days: int = 0
    min_overlap_days: int = 0
    freeze: Optional[tuple] = None  # (start_day, end_day) blackout window
    can_use_view: bool = False
    can_use_snapshot: bool = False
    hard_dependency: set = field(default_factory=set)  # columns it cannot replace


@dataclass
class Response:
    consumer: str
    accept: bool
    issues: list  # each: {type, ask, text}


@dataclass
class Message:
    round: int
    speaker: str
    role: str  # producer | consumer | system
    kind: str  # propose | object | counter | accept | escalate | info
    text: str


@dataclass
class Agreement:
    change: Change
    effective_day: int
    overlap_days: int
    compat_view: bool
    view_days: int
    snapshot: bool
    signed_by: list
    new_contract: Contract


@dataclass
class Result:
    status: str  # agreed | escalated
    messages: list
    rounds: list  # per round: {round, proposal, responses}
    affected: list
    unaffected: list
    agreement: Optional[Agreement] = None
    open_issues: list = field(default_factory=list)
