#!/usr/bin/env bash
set -euo pipefail
python3 -m unittest discover -s branchlab/tests -v
cargo test --manifest-path branchlab/Cargo.toml
cargo run --release --manifest-path branchlab/Cargo.toml -- local --runs 2 --fanout 2 --size-kib 4 --output results/state-local.json
