#!/usr/bin/env bash
# run_soy_export_refresh.sh: refresh the US soybean export numbers behind the Draw Territory tab's export scenario panel
# (data/draw/soy_exports_wasde.json and data/draw/soy_export_shares_by_my.csv). Run by cron on the JSA droplet through
# /opt/alerting/cron-alert on the 12th (15:30 CT) and the 14th (09:00 CT), right after the WASDE and the PSD history refreshes.
# The droplet job clone is /opt/rma-export-refresh, a clone of this repo that only this job commits to.
#
# It pulls GitHub's master, asks USDA's PSD API for US soybean exports and FGIS for inspections by region
# (build_soy_export_shares.py), and only when either file differs from what is committed it commits those files and pushes to
# master. Streamlit Cloud then redeploys. The live droplet app (/opt/jpsi/rma-county-map) is brought up to date with a fast-forward
# pull; export_scenario.py re-reads the files when they change, so the app needs no restart. A reply that looks partial (fewer
# years than on file, a non-positive number, FGIS and USDA more than 20% apart) makes the build exit 1, which fires cron-alert's
# failure email and leaves the committed files alone. The admin portal's apps/rma_map is a generated mirror and still needs
# its usual sync to pick the files up.
#
# FAS_API_KEY is read from /root/corn-exporter-dashboard/.env (one copy to rotate) into this process only, never into the repo or log.
# Everything is inside main() so a pull that rewrites this script mid-run cannot make bash execute half-old, half-new lines.
# Testing: SOY_NO_PUSH=1 commits locally and skips the push; SOY_REPO / SOY_PY / SOY_ENV_FILE / SOY_LIVE point it at scratch clones
# (with a scratch "origin") so the commit-and-push path can be exercised without touching GitHub; SOY_LIVE="" skips the live pull.
set -euo pipefail

main() {
  local REPO="${SOY_REPO:-/opt/rma-export-refresh}"
  local PY="${SOY_PY:-/root/corn-exporter-dashboard/.venv/bin/python}"
  local ENV_FILE="${SOY_ENV_FILE:-/root/corn-exporter-dashboard/.env}"
  local LIVE="${SOY_LIVE-/opt/jpsi/rma-county-map}"
  mkdir -p "$REPO/logs"
  exec >> "$REPO/logs/soy_export_$(date +%F).log" 2>&1
  echo "[$(date '+%F %T')] soy export share refresh"
  exec 9> "$REPO/logs/.soy_export.lock"
  flock -n 9 || { echo "another refresh is still running"; return 0; }

  cd "$REPO"
  # A job clone: only this job commits here, so start from GitHub's master (an unpushed commit from a failed push is regenerated).
  git fetch -q origin master
  git reset -q --hard origin/master

  local key
  key=$(sed -n 's/^FAS_API_KEY=//p' "$ENV_FILE" | head -n 1 | tr -d "\"'\r")
  [ -n "$key" ] || { echo "no FAS_API_KEY in $ENV_FILE"; return 1; }
  FAS_API_KEY="$key" "$PY" build_soy_export_shares.py

  if git diff --quiet -- data/draw/soy_exports_wasde.json data/draw/soy_export_shares_by_my.csv; then
    echo "nothing to commit"
  else
    git config user.email '275148418+koltenpostin93-blip@users.noreply.github.com'
    git config user.name 'JSA Droplet'
    git add data/draw/soy_exports_wasde.json data/draw/soy_export_shares_by_my.csv
    git commit -q -m "data: refresh US soybean export numbers and Gulf shares from USDA PSD and FGIS ($(date +%F))"
    if [ "${SOY_NO_PUSH:-0}" = 1 ]; then
      echo "SOY_NO_PUSH=1: committed locally, not pushed"
      return 0
    fi
    git push -q origin HEAD:master || { git pull -q --rebase origin master && git push -q origin HEAD:master; }
    echo "pushed: $(git log --oneline -1)"
  fi

  # Bring the live droplet app up to date (a fast-forward only: it has no local changes to tracked files).
  if [ -n "$LIVE" ] && [ -d "$LIVE/.git" ]; then
    git -C "$LIVE" pull -q --ff-only origin master && echo "live clone at $(git -C "$LIVE" log --oneline -1)" \
      || echo "WARNING: live clone did not fast-forward; deploy by hand"
  fi
}

main "$@"
exit 0
