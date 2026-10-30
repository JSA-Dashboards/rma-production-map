#!/usr/bin/env bash
# run_canada_refresh.sh: refresh US soybean exports to Canada (Census customs-district data) behind the Draw Territory tab's Canada gateway hubs
# (data/draw/canada_soy_census.json -> canada_soy_by_my.csv -> the Canada columns of soy_export_shares_by_my.csv). Run by cron on the JSA droplet through
# /opt/alerting/cron-alert on weekdays 4th-12th of each month at 11:00 CT, the days Census posts a month's trade detail (Sep data 2026-11-04, Oct 12-08, Nov 2027-01-07,
# Dec 02-04); nearly every run finds nothing new and changes nothing.
# It uses the same job clone as run_soy_export_refresh.sh (/opt/rma-export-refresh, only these jobs commit there) and the same lock.
#
# Steps: pull GitHub's master; ask Census for the Canada soybean data (build_canada_census.py); only if that file changed, rebuild the marketing-year table
# (build_canada_exports.py) and the share history (build_soy_export_shares.py --from-wasde, no key); commit the changed files and push to master (Streamlit Cloud then
# redeploys); fast-forward the live clone /opt/jpsi/rma-county-map (export_scenario.py re-reads files by mtime, but the hub targets live in river_nodes.json, which the
# job also rebuilds, so the live app is restarted when that changed). A bad answer (no key, no rows, fewer months than on file) makes a builder exit 1, which fires
# cron-alert's failure email and leaves the committed files alone. The admin portal's apps/rma_map is a generated mirror and still needs its usual sync.
#
# CENSUS_API_KEY is read from /root/corn-exporter-dashboard/.env into this process only, never into the repo or the log.
# Everything is inside main() so a pull that rewrites this script mid-run cannot make bash execute half-old, half-new lines.
# Testing: CAN_NO_PUSH=1 commits locally and skips the push; CAN_REPO / CAN_PY / CAN_ENV_FILE / CAN_LIVE point it at scratch clones; CAN_LIVE="" skips the live pull.
set -euo pipefail

main() {
  local REPO="${CAN_REPO:-/opt/rma-export-refresh}"
  local PY="${CAN_PY:-/root/corn-exporter-dashboard/.venv/bin/python}"
  local ENV_FILE="${CAN_ENV_FILE:-/root/corn-exporter-dashboard/.env}"
  local LIVE="${CAN_LIVE-/opt/jpsi/rma-county-map}"
  mkdir -p "$REPO/logs"
  exec >> "$REPO/logs/canada_$(date +%F).log" 2>&1
  echo "[$(date '+%F %T')] canada soy export refresh"
  exec 9> "$REPO/logs/.soy_export.lock"
  flock -n 9 || { echo "another refresh is still running"; return 0; }

  cd "$REPO"
  git fetch -q origin master
  git reset -q --hard origin/master

  local key
  key=$(sed -n 's/^CENSUS_API_KEY=//p' "$ENV_FILE" | head -n 1 | tr -d "\"'\r")
  [ -n "$key" ] || { echo "no CENSUS_API_KEY in $ENV_FILE"; return 1; }
  CENSUS_API_KEY="$key" "$PY" build_canada_census.py

  if git diff --quiet -- data/draw/canada_soy_census.json; then
    echo "nothing to commit"
    return 0
  fi
  "$PY" build_canada_exports.py > /dev/null
  "$PY" build_soy_export_shares.py --from-wasde
  "$PY" build_river_segments.py > /dev/null          # the Canada hub targets (river_nodes.json) are the 2021-25 average of the new table

  local files="data/draw/canada_soy_census.json data/draw/canada_soy_by_my.csv data/draw/soy_export_shares_by_my.csv data/draw/river_nodes.json"
  git config user.email '275148418+koltenpostin93-blip@users.noreply.github.com'
  git config user.name 'JSA Droplet'
  # shellcheck disable=SC2086
  git add $files
  git commit -q -m "data: refresh US soybean exports to Canada by gateway from Census ($(date +%F))"
  if [ "${CAN_NO_PUSH:-0}" = 1 ]; then
    echo "CAN_NO_PUSH=1: committed locally, not pushed"
    return 0
  fi
  git push -q origin HEAD:master || { git pull -q --rebase origin master && git push -q origin HEAD:master; }
  echo "pushed: $(git log --oneline -1)"

  if [ -n "$LIVE" ] && [ -d "$LIVE/.git" ]; then
    git -C "$LIVE" pull -q --ff-only origin master && echo "live clone at $(git -C "$LIVE" log --oneline -1)" \
      || echo "WARNING: live clone did not fast-forward; deploy by hand"
    systemctl restart jpsi-rma && echo "restarted jpsi-rma" || echo "WARNING: could not restart jpsi-rma"
  fi
}

main "$@"
exit 0
