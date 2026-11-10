# Contract Court

When a team changes a data column, the teams that depend on it usually find out after their reports break.
Contract Court measures which downstream reports a change would break, lets the affected teams negotiate a
safe plan, and records the agreement as a contract that blocks changes that ignore it.

## What it does

1. **Measure.** In Databricks, each column of each table is dropped, renamed, retyped and rescaled in a
   sandbox copy. Every downstream report is re-run with its own SQL and compared with its baseline. A report is
   affected if it fails or silently returns different results.
2. **Negotiate.** Each affected report becomes an agent with its own needs: notice, how long it needs the old
   column, and freeze windows. The table owners make concessions until every team agrees, or the case is
   escalated to a person when no deal is possible. The negotiation can run on fixed rules or with a Databricks
   model; fixed rules always decide whether the final deal is valid.
3. **Enforce.** The deal becomes a versioned contract. A migration that conflicts with it is blocked.

## Repository layout

| Path | Contents |
|---|---|
| `app/` | Streamlit app and the data it reads (`app/data/snapshot.json`) |
| `engine/` | Measurement, negotiation and contract logic. It does not depend on Databricks. |
| `notebooks/` | Databricks notebooks that produce the snapshot |
| `tests/` | Tests, run on a local DuckDB stand-in |
| `tools/` | Local stand-in warehouse and snapshot builder |
| `deploy.sh` | Deploys the app as a Databricks App |
| `build_notebooks.py` | Re-embeds `engine/` into `notebooks/00_install_engine.py` |

## Run the app

```bash
pip install -r app/requirements.txt
cd app && python3 -m streamlit run app.py
```

The app only reads `app/data/snapshot.json`. It does not connect to Databricks or to a model. The
model-negotiated transcripts in the snapshot were computed in advance by notebook 03.

## Rebuild the snapshot on Databricks

Requires a workspace with Unity Catalog and serverless compute.

1. Import the files in `notebooks/` into a workspace folder.
2. Run `01_build_workload`. It copies a sample of each table in `samples.wanderbricks` into a schema named
   `court_lab` and generates the downstream reports from the schema.
3. Run `02_measure_and_export`. It checks that the sandbox reacts to a change, runs the impact tests, saves the
   `impact_matrix` table, and exports `snapshot.json` to a Volume at
   `/Volumes/<catalog>/court_lab/exports/`.
4. Optional: run `03_llm_negotiations`. It asks a model endpoint to negotiate the highest-impact changes,
   logs prompts and replies to MLflow, and adds the transcripts to `snapshot.json`. Set `ENDPOINT` at the top
   to a chat model endpoint available in your workspace.
5. Copy `snapshot.json` into `app/data/`. `deploy.sh` does this step for you.

## Deploy

```bash
databricks auth login --host https://<your-workspace>
./deploy.sh            # set DATABRICKS_CATALOG=<name> if your catalog is not "workspace"
```

Databricks Apps can only be opened by users signed in to the workspace, and apps on Free Edition stop after
24 hours. Because the app only reads `app/data/snapshot.json`, any Streamlit host can serve it too.

## Tests

```bash
pip install -r requirements-dev.txt
python3 -m pytest tests
```

## How it works

**Measurement.** Each table `T` is a view over a physical copy `T__orig`. To test a change, the view `T` is
redefined with the change applied and every report that depends on `T` is re-run. Results are compared with the
baseline and classified as `ok`, `error` (the report fails) or `silent` (it runs but returns different results).
Chains of reports are handled because the reports are real views over each other.

**Negotiation.** Each team's default needs come from its refresh cycle `c` in days and the number of report
layers `d` built on it:

```
notice  = min(30, max(1, ceil(2c)) + 7d)  days
overlap = min(60, max(1, ceil(3c)) + 7d)  days
```

Any value can be edited in the app. The owners concede in a fixed order: a later date, a longer overlap, a
compatibility view, then a snapshot. With a model, the model writes each team's lines and proposes the owners'
counter-offers. Lines that change a number or date, and offers outside the owners' limits, are discarded and
replaced by the rule-based version.

**Enforcement.** `engine.check_change` rejects a change to a column that has no agreed deprecation window, or
one scheduled before that window ends. A migration job must call it for the check to apply.

## Included snapshot

`app/data/snapshot.json` was produced on Databricks from `samples.wanderbricks`: 16 tables, 33 reports, 763
impact tests, 76 columns that no report reads, and 2,000 sample rows per table. Six changes were negotiated by
`databricks-meta-llama-3-3-70b-instruct` through `ai_query`.

## Limitations

- The data is Databricks' simulated sample data, and the reports are generated from the schema.
- Each team's needs are derived defaults. They cannot be observed from the data, so the app lets you edit them.
- Four kinds of change are measured: rename, drop, retype and rescale.
- Model-negotiated transcripts exist only for the six changes in the snapshot, using default settings.
- This project is not affiliated with or endorsed by Databricks.
