# Databricks notebook source
# MAGIC %md
# MAGIC # Step 3: LLM-negotiated deals
# MAGIC For the highest-impact changes (picked automatically from the measurements), a Databricks model writes each
# MAGIC team's lines and proposes the producer's counter-offers. The rule engine stays the referee: it decides whether
# MAGIC a deal is acceptable, and any invalid model output falls back to the rule-based offer.
# MAGIC Prompts and replies are saved to MLflow. Results are added to `snapshot.json`.

# COMMAND ----------
# MAGIC %run ./00_install_engine

# COMMAND ----------
import json
import mlflow
from engine import Change, Column, Contract, Producer, negotiate, profiles, serialize
from engine.llm import LLM, spark_ask

ENDPOINT = "databricks-meta-llama-3-3-70b-instruct"  # any chat model endpoint in Serving
TOP_K = 6  # how many changes to negotiate
PRODUCER_LIMITS = (30, 90, 60)  # max overlap days, max view days, deadline
EFFECTIVE_DAY = 3

catalog = spark.sql("SELECT current_catalog()").collect()[0][0]
path = f"/Volumes/{catalog}/court_lab/exports/snapshot.json"
snap = json.load(open(path))
by_name = {c["name"]: c for c in snap["consumers"]}
me = spark.sql("SELECT current_user()").collect()[0][0]
mlflow.set_experiment(f"/Users/{me}/contract-court-experiments")

# COMMAND ----------
# MAGIC %md ## Pick the changes automatically: the columns whose change hurts the most consumers

# COMMAND ----------
def candidates():
    hits = {}
    for r in snap["impact"]:
        if r["kind"] == "drop" and r["mode"] == "no_contract" and r["outcome"] != "ok":
            hits.setdefault((r["table"], r["column"]), set()).add(r["consumer"])
    for (table, column), who in sorted(hits.items(), key=lambda kv: -len(kv[1])):
        kinds = {r["kind"] for r in snap["impact"] if (r["table"], r["column"]) == (table, column)}
        yield table, column, Change("rename_column", column, EFFECTIVE_DAY, new_name=f"{column}_v2")
        if "meaning" in kinds:
            yield table, column, Change("change_meaning", column, EFFECTIVE_DAY, factor=100.0)

def signature(table, ch):
    param = ch.new_dtype if ch.kind == "change_type" else ""
    return f"{table}|{ch.column}|{profiles.KIND_OF[ch.kind]}|{param}"

chosen = []
for table, column, ch in candidates():
    if len(chosen) >= TOP_K:
        break
    chosen.append((table, ch))
print([signature(t, c) for t, c in chosen])

# COMMAND ----------
# MAGIC %md ## Negotiate with the model

# COMMAND ----------
out = {}
for table, ch in chosen:
    rows = profiles.affected(snap, table, ch)
    names = {r["consumer"] for r in rows}
    agents = [profiles.build_consumer(by_name[n]) for n in sorted(names)]
    others = [c["name"] for c in snap["consumers"] if c["name"] not in names]
    cols = snap["tables"][table]
    llm = LLM(spark_ask(spark, ENDPOINT))
    res = negotiate(Contract(table, 1, {n: Column(n, t) for n, t in cols}), ch, agents, others,
                    Producer(f"{table} owners", *PRODUCER_LIMITS), llm=llm,
                    facts={r["consumer"]: r["detail"] for r in rows})
    out[signature(table, ch)] = {"result": serialize.result_to_dict(res)}
    with mlflow.start_run(run_name=signature(table, ch)):
        mlflow.log_params({"endpoint": ENDPOINT, "change": ch.describe(), "status": res.status})
        mlflow.log_metrics({"rounds": len(res.rounds), "model_calls": len(llm.log),
                            "model_errors": sum(1 for _, _, r in llm.log if r.startswith("ERROR"))})
        mlflow.log_text(json.dumps(llm.log, indent=2), "prompts_and_replies.json")
    print(f"{signature(table, ch)}: {res.status}, {len(res.rounds)} rounds, {len(llm.log)} model calls")

# COMMAND ----------
snap["llm_negotiations"] = out
snap["llm_endpoint"] = ENDPOINT
with open(path, "w") as f:
    json.dump(snap, f)
print("saved", len(out), "LLM negotiations into", path)
