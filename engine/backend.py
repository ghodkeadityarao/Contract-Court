"""Thin SQL backends. The same pipeline runs on DuckDB (local tests) and Spark (Databricks)."""
from __future__ import annotations

import re

_NUMERIC = re.compile(r"^(u?(tiny|small|big|huge)?int(eger)?|long|float|double|decimal|numeric|real)\b")


def category(sql_type: str) -> str:
    """numeric | string | temporal | bool | other"""
    t = sql_type.lower().strip()
    if re.match(r"^(struct|array|map|row|union|variant|binary|blob|interval|object)\b", t) or "<" in t or t.endswith("[]"):
        return "other"
    if t.startswith("bool"):
        return "bool"
    if "timestamp" in t or t.startswith("date"):
        return "temporal"
    if any(k in t for k in ("char", "string", "text")):
        return "string"
    if _NUMERIC.match(t):
        return "numeric"
    return "other"


class DuckBackend:
    def __init__(self, conn):
        self.con = conn

    def run(self, sql: str) -> list:
        return self.con.execute(sql).fetchall()

    def exec(self, sql: str) -> None:
        self.con.execute(sql)

    def columns(self, table: str) -> list:
        return [(r[0], r[1]) for r in self.con.execute(f"DESCRIBE {table}").fetchall()]


class SparkBackend:
    def __init__(self, spark):
        self.spark = spark

    def run(self, sql: str) -> list:
        return [tuple(r) for r in self.spark.sql(sql).collect()]

    def exec(self, sql: str) -> None:
        self.spark.sql(sql)

    def columns(self, table: str) -> list:
        return [(f.name, f.dataType.simpleString()) for f in self.spark.table(table).schema.fields]
