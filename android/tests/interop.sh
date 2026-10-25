#!/usr/bin/env bash
# Developer test: a Kotlin "phone" and the Python "PC" sync through a local ntfy-compatible relay.
# Needs: kotlinc (+ a JRE) and python3 with `cryptography`.   KOTLINC=/path/to/kotlinc ./interop.sh
set -eu   # (no pipefail: grep -q closes the pipe early)
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
KOTLINC="${KOTLINC:-kotlinc}"
WORK="$(mktemp -d)"; JAR="$WORK/enginetest.jar"
"$KOTLINC" "$ROOT"/android/engine/*.kt "$ROOT/android/tests/EngineTest.kt" -include-runtime -d "$JAR" 2>/dev/null
export PYTHONPATH="$ROOT"
PORT=$((20000 + RANDOM % 10000)); SRV="http://127.0.0.1:$PORT"
python3 "$ROOT/tests/fake_ntfy.py" "$PORT" >/dev/null 2>&1 & SP=$!
trap 'kill $SP 2>/dev/null || true' EXIT
sleep 1
PC="$WORK/pc"; PHD="$WORK/phone"; mkdir -p "$PC"
pc(){ HOME="$PC" python3 "$ROOT/etimer" "$@"; }
ph(){ java -jar "$JAR" "$1" "$PHD" "${@:2}"; }
ok(){ echo "  ok: $1"; }
bad(){ echo "  FAIL: $1"; exit 1; }
expect(){ if eval "$2" >/dev/null 2>&1; then ok "$1"; else bad "$1"; fi; }

java -jar "$JAR" vectors "$ROOT/tests/vectors.json" >/dev/null && ok "crypto vectors identical to Python"
pc add timer 30m "deep work" >/dev/null; pc add stopwatch "reading" >/dev/null
CODE=$(pc sync setup "$SRV" | grep -E '^\s+[A-Z2-7]{4}-' | tr -d ' ')
ph join "$CODE" "$SRV" >/dev/null
expect "phone received the PC's items on join" 'ph list | grep -q "deep work"'
ph pause "deep work"; ph sync >/dev/null; pc sync now >/dev/null
expect "phone's pause reached the PC" 'pc list | grep -q "paused"'
pc remove reading >/dev/null; pc sync now >/dev/null; ph sync >/dev/null
expect "PC's delete reached the phone (tombstone)" '! ph list | grep -q reading'
ph rename "deep work" "Deep (renamed on phone)"; pc resume "deep work" >/dev/null   # concurrent edits
ph sync >/dev/null; pc sync now >/dev/null; ph sync >/dev/null; pc sync now >/dev/null
expect "concurrent rename+resume: PC has both" 'pc list | grep -q "renamed on phone.*running"'
expect "concurrent rename+resume: phone has both" 'ph list | grep -q "renamed on phone.*running"'
ph add timer 2 "tea" >/dev/null; ph sync >/dev/null; pc sync now >/dev/null; sleep 3; ph finish >/dev/null
ph sync >/dev/null; pc sync now >/dev/null
expect "phone's finish event shows in the PC's history" 'pc history tea | grep -q Finished'
(ph live 6 >/dev/null 2>&1 &); sleep 2.5
pc add timer 45m "pushed-while-live" >/dev/null; pc sync now >/dev/null; sleep 4.5
expect "live connection received a PC-created timer" 'ph list | grep -q pushed-while-live'
echo "ALL INTEROP CHECKS PASSED"
