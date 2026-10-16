#!/bin/sh
# Container entry point of the indexer: runs forever.
#   - an incremental run every INTERVAL_SECONDS (default 300);
#   - a reconciliation once a day, at the first loop at or after RECONCILE_AT (HH:MM, container local time, default
#     03:00). The day of the last reconciliation is kept in STATE_DIR/last_reconcile; on the very first start after
#     RECONCILE_AT, today's reconciliation is skipped (the incremental runs are filling the index anyway).
# An indexer failure is logged and the loop goes on; only invalid INTERVAL_SECONDS or RECONCILE_AT stop it (exit 2).
# Extra arguments are passed to every indexer run (e.g. --env-file F outside Docker).
# For tests only: INDEXER_CMD replaces "python3 <dir>/indexer.py", RUN_LOOPS > 0 stops after that many loops.
set -u

DIR=$(cd "$(dirname "$0")" && pwd)
INTERVAL=${INTERVAL_SECONDS:-300}
AT=${RECONCILE_AT:-03:00}
STATE=${STATE_DIR:-/state}
CMD=${INDEXER_CMD:-python3 $DIR/indexer.py}
LOOPS=${RUN_LOOPS:-0}

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') run.sh: $*"; }

case "$INTERVAL" in
  ''|*[!0-9]*) log "configuration error: INTERVAL_SECONDS must be a positive integer"; exit 2 ;;
esac
[ "$INTERVAL" -gt 0 ] || { log "configuration error: INTERVAL_SECONDS must be a positive integer"; exit 2; }
case "$AT" in
  [0-9]:[0-5][0-9]|[01][0-9]:[0-5][0-9]|2[0-3]:[0-5][0-9]) ;;
  *) log "configuration error: RECONCILE_AT must be HH:MM (24 h)"; exit 2 ;;
esac

# minutes since midnight, without octal surprises on 08 and 09
minutes() { h=${1%%:*}; m=${1#*:}; h=${h#0}; m=${m#0}; echo $(( ${h:-0} * 60 + ${m:-0} )); }
AT_MIN=$(minutes "$AT")
MARK="$STATE/last_reconcile"

stop() { log "stopping"; exit 0; }
trap stop TERM INT

run_indexer() {  # $1 = --incremental or --reconcile, then the extra arguments
  # shellcheck disable=SC2086
  $CMD "$@"
  rc=$?
  case $rc in
    0) ;;
    3) log "$1: another run holds the lock, skipped" ;;
    *) log "$1: indexer exited with code $rc, continuing" ;;
  esac
}

mkdir -p "$STATE" 2>/dev/null
if [ ! -s "$MARK" ] && [ "$(minutes "$(date +%H:%M)")" -ge "$AT_MIN" ]; then
  date +%Y-%m-%d > "$MARK" 2>/dev/null || log "cannot write $MARK"
fi
log "started: incremental every ${INTERVAL}s, reconciliation daily at $AT"

n=0
while :; do
  run_indexer --incremental "$@"
  today=$(date +%Y-%m-%d)
  last=$(cat "$MARK" 2>/dev/null || true)
  if [ "$last" != "$today" ] && [ "$(minutes "$(date +%H:%M)")" -ge "$AT_MIN" ]; then
    run_indexer --reconcile "$@"
    # a lock held by a manual run: try again at the next loop; any other outcome counts as today's reconciliation
    if [ "$rc" -ne 3 ]; then
      echo "$today" > "$MARK" 2>/dev/null || log "cannot write $MARK"
    fi
  fi
  n=$((n + 1))
  if [ "$LOOPS" -gt 0 ] && [ "$n" -ge "$LOOPS" ]; then
    exit 0
  fi
  sleep "$INTERVAL" &
  wait $!
done
