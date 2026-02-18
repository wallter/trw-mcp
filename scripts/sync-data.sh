#!/bin/bash
# Sync .claude/skills and .claude/agents to data/ for pip packaging.
# Run from repo root before releases.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DATA_DIR="$REPO_ROOT/trw-mcp/src/trw_mcp/data"

rm -rf "$DATA_DIR/skills" "$DATA_DIR/agents"

# Copy only the 10 lifecycle skills (exclude trw-simplify which is repo-local)
for skill in deliver framework-check memory-audit memory-optimize prd-groom prd-new prd-review sprint-finish sprint-init test-strategy; do
    cp -r "$REPO_ROOT/.claude/skills/$skill" "$DATA_DIR/skills/$skill"
done

cp -r "$REPO_ROOT/.claude/agents" "$DATA_DIR/agents"

echo "Synced skills ($(ls -d "$DATA_DIR/skills"/*/ | wc -l) dirs) and agents ($(ls "$DATA_DIR/agents"/*.md | wc -l) files)"
