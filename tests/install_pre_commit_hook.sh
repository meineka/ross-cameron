#!/usr/bin/env bash
# install_pre_commit_hook.sh — installiert Quality-Gate als Git-pre-commit-Hook.
# Nach Installation: jeder `git commit` triggert pytest automatisch.
# Wenn Tests fail → commit aborted.

set -e
HOOK_PATH=".git/hooks/pre-commit"
ROOT=$(git rev-parse --show-toplevel)
cd "$ROOT"

cat > "$HOOK_PATH" <<'EOF'
#!/usr/bin/env bash
# Auto-generated pre-commit hook: Cameron-Bot Quality Gates

echo ""
echo "================================================"
echo "Running Cameron-Bot Quality Gates..."
echo "================================================"

# Fast subset für pre-commit (skip replay-tests die 3min brauchen)
python -m pytest tests/ -q --tb=line \
    --ignore=tests/test_replay_regression.py \
    -k "not scan_only"

if [ $? -ne 0 ]; then
    echo ""
    echo "================================================"
    echo "  QUALITY GATE FAILED — commit aborted"
    echo "  Fix tests, then 'git commit' again"
    echo "  Full details: python -m pytest tests/ -v"
    echo "================================================"
    exit 1
fi

echo ""
echo "  Quality gates passed."
echo ""
EOF

chmod +x "$HOOK_PATH"
echo "Pre-commit hook installiert: $HOOK_PATH"
echo "Test mit: 'git commit --allow-empty -m test'"
