#!/usr/bin/env bash
# Measures how loaded the server already is, before installing the search engine. Read only: it samples available
# RAM, free disk and load average every 5 minutes, writes them to a CSV, and prints a summary at the end. Nothing
# else is touched. Run it in the background for a few hours, ideally across the busiest part of the day:
#
#   nohup ./load_probe.sh 4 /var/lib/docker > probe.log 2>&1 &
#
# Arguments: hours (default 4), the directory whose disk would hold the engine data (default: here).
# Output: load_probe-<date>.csv here, and the summary at the end of the run (also in probe.log above).
set -euo pipefail
cd "$(dirname "$0")"
HOURS=${1:-4}
DIR=${2:-.}
STEP=300
[[ "$HOURS" =~ ^[0-9]+$ ]] && (( HOURS > 0 )) || { echo "hours: a positive integer" >&2; exit 2; }
[ -d "$DIR" ] || { echo "no such directory: $DIR" >&2; exit 2; }
OUT="load_probe-$(date +%Y%m%d-%H%M).csv"
CPUS=$(nproc)
echo "time,mem_available_mb,disk_free_mb,load1,load5,load15" > "$OUT"

sample() {
	local mem disk load
	mem=$(awk '/^MemAvailable:/ {printf "%d", $2/1024}' /proc/meminfo)
	disk=$(df -Pk "$DIR" | awk 'NR==2 {printf "%d", $4/1024}')
	load=$(awk '{print $1","$2","$3}' /proc/loadavg)
	echo "$(date -Is),$mem,$disk,$load" >> "$OUT"
}

end=$(( $(date +%s) + HOURS * 3600 ))
sample
while (( $(date +%s) < end )); do
	sleep "$STEP"
	sample
done

awk -F, -v cpus="$CPUS" 'NR > 1 {
		n++; m = $2; d = $3; l = $6
		if (n == 1 || m < mmin) mmin = m; msum += m
		if (n == 1 || d < dmin) dmin = d
		if (l > lmax) lmax = l; lsum += l; if (l > cpus) over++
	}
	END {
		printf "samples: %d over %s\n", n, (n > 1 ? (n - 1) * 5 " minutes" : "one point")
		printf "available RAM: min %d MB, average %d MB\n", mmin, msum / n
		printf "free disk: min %d MB\n", dmin
		printf "load (15 min) on %d CPUs: average %.2f, max %.2f, above the CPU count in %d%% of samples\n", cpus, lsum / n, lmax, 100 * over / n
	}' "$OUT"
echo "details: $(pwd)/$OUT"
