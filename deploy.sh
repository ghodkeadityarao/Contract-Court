#!/usr/bin/env bash
# Deploy the Contract Court web app to your Databricks workspace.
# One-time setup:
#   brew tap databricks/tap && brew install databricks
#   databricks auth login --host https://YOUR-WORKSPACE-URL     (opens a browser, click Allow)
# Then:  ./deploy.sh
# If your catalog is not "workspace", run:  DATABRICKS_CATALOG=your_catalog ./deploy.sh
set -euo pipefail
cd "$(dirname "$0")"

APP_NAME="${APP_NAME:-contract-court}"
ME="$(databricks current-user me | python3 -c 'import sys,json; print(json.load(sys.stdin)["userName"])')"
WS_DIR="/Workspace/Users/${ME}/contract-court-app"
BUILD="$(mktemp -d)"

echo "0/4 Fetching the snapshot exported by notebook 02 (the existing app/data/snapshot.json is kept if this fails)"
CATALOG="${DATABRICKS_CATALOG:-workspace}"
mkdir -p app/data
databricks fs cp "dbfs:/Volumes/${CATALOG}/court_lab/exports/snapshot.json" "app/data/snapshot.json" --overwrite \
  || echo "  could not fetch snapshot.json, keeping the existing one"

echo "1/4 Bundling app + engine in ${BUILD}"
cp -R app/. "${BUILD}/"
cp -R engine "${BUILD}/engine"
rm -rf "${BUILD}/__pycache__" "${BUILD}/engine/__pycache__"

echo "2/4 Creating the app (an 'already exists' message is fine)"
databricks apps create "${APP_NAME}" || true

echo "3/4 Uploading files to ${WS_DIR}"
databricks sync --full "${BUILD}" "${WS_DIR}"

echo "4/4 Starting the app if it is stopped (Free Edition stops apps after 24 hours), then deploying"
databricks apps start "${APP_NAME}" || echo "  (already running, or could not start: continuing)"
databricks apps deploy "${APP_NAME}" --source-code-path "${WS_DIR}"

echo
echo "Done. Open it: workspace left sidebar > Compute > Apps > ${APP_NAME}"
echo "Free Edition apps stop after 24 hours. Run ./deploy.sh again to restart and redeploy."
