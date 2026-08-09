#!/usr/bin/env bash
set -euo pipefail

echo "🗑️  Uninstalling tick-proxy..."
uv tool uninstall tick-proxy 2>/dev/null || true
rm -rf ~/.config/tick-proxy
echo "✅ tick-proxy fully removed."
