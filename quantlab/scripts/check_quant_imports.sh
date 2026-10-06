#!/usr/bin/env bash
# quant legacy shim import 守卫（P39，非破坏性）

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
exec python3 quant/ops/shim_audit.py "$@"
