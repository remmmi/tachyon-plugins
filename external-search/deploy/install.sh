#!/usr/bin/env bash
# Installs the search engine and the indexer for external-search, one step at a time. Each step is idempotent
# and prints only what is missing or wrong; run them in order, after agreeing to each (see AGENTS.md).
#
#   ./install.sh check                          Docker, Compose, port, disk: report, change nothing
#   ./install.sh init [--tachyon-network NAME]  folders, .env, indexer.env, engine master key (no container yet)
#   ./install.sh engine                         start Meilisearch, create the index and its two keys
#   ./install.sh indexer                        one short test run, then start the indexer service
#   ./install.sh status                         engine health, index check, last indexer run, plugin settings
#   ./install.sh down                           stop both containers (data kept)
#
# Never prints a key or a password: it names the file to read instead.
set -euo pipefail
cd "$(dirname "$0")"

say() { printf '%s\n' "$*"; }
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
env_get() { sed -n "s/^$1=//p" "$2" 2>/dev/null | tail -1; }
compose() { docker compose "$@"; }

port() { local p; p=$(env_get MEILI_PORT .env); echo "${p:-7700}"; }

wait_health() {
	local p; p=$(port)
	for _ in $(seq 1 60); do
		curl -fsS "http://127.0.0.1:$p/health" >/dev/null 2>&1 && return 0
		sleep 1
	done
	return 1
}

cmd_check() {
	local bad=0
	command -v docker >/dev/null || { say "missing: docker (see AGENTS.md, 'Without Docker')"; bad=1; }
	if command -v docker >/dev/null; then
		docker compose version >/dev/null 2>&1 || { say "missing: docker compose v2 plugin"; bad=1; }
		docker info >/dev/null 2>&1 || { say "docker daemon not reachable by $(id -un) (group docker, or sudo)"; bad=1; }
	fi
	command -v curl >/dev/null || { say "missing: curl"; bad=1; }
	local p; p=$(port)
	if ! [ -f .env ] && (command -v ss >/dev/null && ss -ltn | grep -q "127.0.0.1:$p "); then
		say "port 127.0.0.1:$p already in use: set MEILI_PORT in .env after init"
	fi
	local free_gb; free_gb=$(df -Pk . | awk 'NR==2 {printf "%d", $4/1048576}')
	[ "$free_gb" -ge 5 ] || say "only ${free_gb} GB free on $(pwd): see docs/footprint.md for the projection"
	[ "$bad" -eq 0 ] && say "check: nothing blocking" || exit 1
}

cmd_init() {
	local net=""
	if [ "${1:-}" = "--tachyon-network" ]; then
		net=${2:?network name}
		docker network inspect "$net" >/dev/null 2>&1 || fail "no Docker network named $net (docker network ls)"
	fi
	umask 077
	mkdir -p data/meili data/state keys secrets
	if [ ! -f .env ]; then
		local tz; tz=$(cat /etc/timezone 2>/dev/null || echo UTC)
		printf 'RUN_UID=%s\nRUN_GID=%s\nTZ=%s\nMEILI_PORT=7700\nMEILI_MEM_LIMIT=2g\nMEILI_MAX_INDEXING_MEMORY=1GiB\nMEILI_MAX_INDEXING_THREADS=1\n' \
			"$(id -u)" "$(id -g)" "$tz" > .env
		say "written: .env (edit limits there, see docs/footprint.md)"
	fi
	if [ -n "$net" ]; then
		sed -i '/^TACHYON_NETWORK=/d; /^COMPOSE_FILE=/d' .env
		printf 'TACHYON_NETWORK=%s\nCOMPOSE_FILE=compose.yml:compose.network.yml\n' "$net" >> .env
		say "engine will join Docker network $net (plugin URL: http://meilisearch:7700)"
	fi
	[ -f indexer.env ] || { cp indexer.env.example indexer.env; say "written: indexer.env (fill in the IMAP part)"; }
	if [ ! -s secrets/meili_master_key ]; then
		local key; key=$(head -c 32 /dev/urandom | base64 | tr -d '/+=\n' | head -c 40)
		printf '%s\n' "$key" > secrets/meili_master_key
		printf 'MEILI_MASTER_KEY=%s\n' "$key" > secrets/meili.env
		say "written: secrets/meili_master_key and secrets/meili.env (engine master key, never give it to the webmail)"
	fi
	[ -e secrets/imap_password ] || { : > secrets/imap_password; say "created empty: secrets/imap_password (put the mailbox password in it, one line)"; }
	chmod 600 secrets/* 2>/dev/null || true
	grep -q '^IMAP_HOST=imap.example.com' indexer.env && say "to do: IMAP_HOST and IMAP_USER in indexer.env"
	[ -s secrets/imap_password ] || say "to do: secrets/imap_password"
	return 0
}

cmd_engine() {
	[ -s secrets/meili.env ] || fail "run ./install.sh init first"
	compose up -d meilisearch >/dev/null
	wait_health || fail "Meilisearch did not answer /health within 60 s: docker compose logs meilisearch"
	compose run --rm --no-deps indexer python3 /app/setup_index.py
	say "engine: up on 127.0.0.1:$(port), index ready"
}

cmd_indexer() {
	[ -s secrets/imap_password ] || fail "secrets/imap_password is empty"
	! grep -q '^IMAP_HOST=imap.example.com' indexer.env || fail "IMAP_HOST in indexer.env is still the example"
	wait_health || fail "engine not running: ./install.sh engine"
	say "test run: up to 50 messages"
	compose run --rm indexer python3 /app/indexer.py --incremental --limit 50 || fail "test run failed (output above)"
	compose up -d indexer >/dev/null
	say "indexer: running, incremental every INTERVAL_SECONDS, reconciliation daily at RECONCILE_AT (indexer.env)"
}

cmd_status() {
	local p; p=$(port)
	curl -fsS "http://127.0.0.1:$p/health" >/dev/null 2>&1 || { say "engine: NOT answering on 127.0.0.1:$p"; return 1; }
	compose run --rm --no-deps indexer python3 /app/setup_index.py --check || true
	if [ -f data/state/status.json ]; then
		say "last indexer run: $(tr -d '\n' < data/state/status.json)"
	else
		say "indexer: no run yet"
	fi
	local net; net=$(env_get TACHYON_NETWORK .env)
	say "plugin settings (Admin > Extensions > External Search):"
	say "  Engine URL: $([ -n "$net" ] && echo http://meilisearch:7700 || echo "http://127.0.0.1:$p")"
	say "  API key:    the content of $(pwd)/keys/search.key"
	say "  Status key: the content of $(pwd)/keys/status.key"
	say "  Index name: $(env_get MEILI_INDEX indexer.env)"
}

cmd_down() { compose stop >/dev/null; say "stopped (data kept in data/)"; }

case "${1:-}" in
	check) cmd_check ;;
	init) shift; cmd_init "$@" ;;
	engine) cmd_engine ;;
	indexer) cmd_indexer ;;
	status) cmd_status ;;
	down) cmd_down ;;
	*) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
