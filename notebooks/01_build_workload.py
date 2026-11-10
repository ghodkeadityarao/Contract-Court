# Databricks notebook source
# MAGIC %md
# MAGIC # Step 1: build the sandbox and the downstream workload
# MAGIC Copies a sample of every table in the source schema, then generates dashboards, features, reports and
# MAGIC monitors from the schema alone, including reports built on other reports.

# COMMAND ----------
# MAGIC %run ./00_install_engine

# COMMAND ----------
import re
from engine import measure, workload
from engine.backend import SparkBackend

SOURCE = "samples.wanderbricks"  # any schema works
SAMPLE_ROWS = 2000  # rows copied per table
MAX_CONSUMERS = 30
SAFE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

spark.sql("CREATE SCHEMA IF NOT EXISTS court_lab")
spark.sql("USE SCHEMA court_lab")
backend = SparkBackend(spark)

# COMMAND ----------
tables = {}
for row in spark.sql(f"SHOW TABLES IN {SOURCE}").collect():
    t = row["tableName"]
    spark.sql(f"CREATE OR REPLACE TABLE {t}__orig AS SELECT * FROM {SOURCE}.{t} LIMIT {SAMPLE_ROWS}")
    cols = [(n, ty) for n, ty in backend.columns(f"{t}__orig") if SAFE.match(n)]
    tables[t] = cols
    print(f"{t}: {len(cols)} columns")

# COMMAND ----------
specs = workload.generate(tables, MAX_CONSUMERS)
measure.build_sandbox(backend, tables, specs)
spark.createDataFrame([(s.name, s.sql, s.kind, s.owner, s.cadence_hours, s.level) for s in specs],
                      "name string, sql string, kind string, owner string, cadence_hours int, level int") \
     .write.mode("overwrite").saveAsTable("consumer_registry")
display(spark.table("consumer_registry"))
