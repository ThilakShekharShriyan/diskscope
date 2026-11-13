#!/usr/bin/env bash
# Generate a REAL sampled CPU flamegraph of the Rust harness's hot loop (`diskscope profile-workload`).
# This profiles our own Rust code — not Archil's control plane.
#
#   Linux:  cargo flamegraph (perf)            -> cargo install flamegraph --locked
#   macOS:  cargo flamegraph if full Xcode/xctrace is available, otherwise the built-in
#           `sample` profiler (no root, no Xcode)  -> cargo install inferno --locked
#
# Output: flamegraph.svg in the repo root. Copy it to docs/flamegraph.svg to publish it on the dashboard.
set -euo pipefail
cd "$(dirname "$0")/.."

ITERATIONS="${ITERATIONS:-2000000000}"   # ~several seconds of CPU; capped at 2e9 by the CLI
OUT="flamegraph.svg"
TITLE="diskscope profile-workload (${ITERATIONS} iterations)"

have() { command -v "$1" >/dev/null 2>&1; }

if [[ "$(uname)" == "Darwin" ]] && ! xcrun --find xctrace >/dev/null 2>&1; then
  for tool in inferno-collapse-sample inferno-flamegraph; do
    have "$tool" || { echo "Install first: cargo install inferno --locked" >&2; exit 1; }
  done
  cargo build --release
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' EXIT
  ./target/release/diskscope profile-workload --iterations "$ITERATIONS" &
  pid=$!
  sleep 0.3                                   # let the process reach its hot loop
  # 1 ms sampling interval; `sample` stops on its own when the duration ends or the process exits.
  /usr/bin/sample "$pid" 30 1 -mayDie -file "$tmp/sample.txt" >/dev/null 2>&1 || true
  wait "$pid"
  inferno-collapse-sample "$tmp/sample.txt" > "$tmp/folded.txt"
  inferno-flamegraph --title "$TITLE" --subtitle "macOS sample(1), 1 ms interval" < "$tmp/folded.txt" > "$OUT"
else
  have cargo-flamegraph || { echo "Install first: cargo install flamegraph --locked" >&2; exit 1; }
  cargo build --release
  cargo flamegraph --release --bin diskscope --output "$OUT" -- profile-workload --iterations "$ITERATIONS"
fi

echo "Flamegraph generated: $OUT  (cp $OUT docs/flamegraph.svg to publish it on the dashboard)"
