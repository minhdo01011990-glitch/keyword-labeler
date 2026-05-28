#!/bin/bash
set -e

echo "=== Keyword Labeler Installer ==="

# Detect Python 3.9+
PYTHON=""
for py in python3.13 python3.12 python3.11 python3.10 python3.9 python3 python; do
    if command -v "$py" &>/dev/null; then
        OK=$("$py" -c "import sys; print('ok' if sys.version_info >= (3,9) else '')" 2>/dev/null)
        if [ "$OK" = "ok" ]; then
            PYTHON="$py"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    echo "Error: Python 3.9+ is required but not found."
    exit 1
fi

echo "Using $($PYTHON --version)"

# Install package in editable mode from this directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "Installing from $SCRIPT_DIR ..."
$PYTHON -m pip install -e "$SCRIPT_DIR" --quiet

# Register MCP server into Claude Desktop config
echo "Registering MCP server..."
export KEYWORD_LABELER_PROJECT_DIR="$SCRIPT_DIR"
# Prefer module invocation (works regardless of PATH) with script as fallback
if command -v keyword-labeler-install &>/dev/null; then
    keyword-labeler-install
else
    $PYTHON -m keyword_labeler.install
fi

echo ""
echo "Done! Please restart Claude Desktop to apply changes."
echo "Then type /keyword in Claude to start."
