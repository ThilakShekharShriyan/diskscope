#!/usr/bin/env bash
set -euo pipefail
if ! command -v cargo-flamegraph >/dev/null 2>&1; then
  echo "Install first: cargo install flamegraph" >&2
  exit 1
fi
cargo build --release
cargo flamegraph --release --bin diskscope -- profile-workload --iterations 300000000
printf '%s\n' "Flamegraph generated: flamegraph.svg. Optionally copy to docs/flamegraph.svg, then commit."
