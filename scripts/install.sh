#!/usr/bin/env bash
set -euo pipefail

echo "🚀 Installing tick-proxy..."
uv tool install . --force
echo "✅ tick-proxy installed. Run 'tick-proxy --help' to start."
