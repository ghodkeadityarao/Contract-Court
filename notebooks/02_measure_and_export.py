# Databricks notebook source
# MAGIC %md
# MAGIC # Step 2: measure the real blast radius of every change
# MAGIC Every column is dropped, renamed, retyped and rescaled in the sandbox. After each change every downstream
# MAGIC consumer is re-run with its own SQL and compared with its baseline. Results are saved as a Delta table and
# MAGIC exported as `snapshot.json` for the app.

# COMMAND ----------
# MAGIC %run ./00_install_engine

# COMMAND ----------
import json, re
from engine import measure
from engine.backend import SparkBackend
from engine.workload import Spec

spark.sql("USE SCHEMA court_lab")
backend = SparkBackend(spark)
try:  # a cached result would hide the effect of a changed view
    spark.conf.set("spark.databricks.result.cache.enabled", "false")
except Exception:  # noqa: BLE001
    pass

SAFE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
names = [r["tableName"] for r in spark.sql("SHOW TABLES").collect() if r["tableName"].endswith("__orig")]
tables = {n[:-6]: [(c, t) for c, t in backend.columns(n) if SAFE.match(c)] for n in sorted(names)}
specs = [Spec(r["name"], r["sql"], r["kind"], r["owner"], r["cadence_hours"], r["level"], ())
         for r in spark.table("consumer_registry").collect()]
SAMPLE_ROWS = max(spark.table(n).count() for n in names)

# COMMAND ----------
# MAGIC %md ## Self-check: dropping a column a consumer reads must break that consumer

# COMMAND ----------
consumer, table, column, broke = measure.canary(backend, tables, specs)
print(f"dropping {table}.{column}: {consumer} {'FAILED as expected' if broke else 'DID NOT FAIL'}")
assert broke, "The sandbox did not react to a change. Results would be meaningless, so stopping."

# COMMAND ----------
# MAGIC %md ## Measure (this runs many small queries and can take several minutes)

# COMMAND ----------
result = measure.measure(backend, tables, specs)

# COMMAND ----------
# Optional cross-check against Unity Catalog's own lineage
uc_lineage = []
try:
    cat = spark.sql("SELECT current_catalog()").collect()[0][0]
    rows = spark.sql(f"""
        SELECT DISTINCT source_table_name AS s, target_table_name AS t FROM system.access.table_lineage
        WHERE target_table_catalog = '{cat}' AND target_table_schema = 'court_lab'
          AND source_table_schema = 'court_lab'""").collect()
    uc_lineage = [[r["s"], r["t"]] for r in rows]
    print("Unity Catalog lineage edges:", len(uc_lineage))
except Exception as e:  # noqa: BLE001
    print("Unity Catalog lineage not readable here:", str(e)[:160])

# COMMAND ----------
snapshot = measure.build_snapshot("databricks", tables, specs, result, SAMPLE_ROWS, uc_lineage)
spark.createDataFrame(result["rows"], "table string, column string, kind string, param string, mode string, "
                      "consumer string, outcome string, detail string").write.mode("overwrite").saveAsTable("impact_matrix")

catalog = spark.sql("SELECT current_catalog()").collect()[0][0]
spark.sql("CREATE VOLUME IF NOT EXISTS exports")
export_path = f"/Volumes/{catalog}/court_lab/exports/snapshot.json"
with open(export_path, "w") as f:
    json.dump(snapshot, f)
print("exported", export_path)
print(f"{len(specs)} consumers, {len(result['rows'])} impact rows, {len(result['unread'])} columns nobody reads")

# COMMAND ----------
display(spark.sql("""
  SELECT kind, mode, outcome, COUNT(*) AS n FROM impact_matrix GROUP BY kind, mode, outcome ORDER BY kind, mode, outcome
"""))
