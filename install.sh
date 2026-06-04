#!/usr/bin/env bash
# install.sh — Cài đặt Keyword Labeler Plugin
# Dùng: bash <(curl -sSL https://raw.githubusercontent.com/minhdo01011990-glitch/keyword-labeler/main/install.sh)
set -euo pipefail

BOLD="\033[1m"; GREEN="\033[32m"; RED="\033[31m"; YELLOW="\033[33m"; RESET="\033[0m"

_run_installer() {
    local bin="$1"
    "$bin/keyword-labeler-install" 2>/dev/null && return 0
    return 1
}

# ── Thử uv trước (không phụ thuộc pip/pyexpat) ──────────────────────────────
if command -v uv &>/dev/null; then
    echo -e "${BOLD}Cài đặt keyword-labeler qua uv...${RESET}"
    uv tool install --force keyword-labeler

    echo -e "${BOLD}Chạy installer...${RESET}"
    _run_installer "$HOME/.local/bin" \
        || uv run keyword-labeler-install \
        || { echo -e "${RED}❌ Không tìm thấy keyword-labeler-install sau khi cài qua uv.${RESET}"; exit 1; }
    exit 0
fi

# ── Tìm Python 3.9+ có pip hoạt động ────────────────────────────────────────
PYTHON=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3.9 python3 python; do
    if ! command -v "$candidate" &>/dev/null; then continue; fi

    _major=$("$candidate" -c "import sys; print(sys.version_info.major)" 2>/dev/null || echo 0)
    _minor=$("$candidate" -c "import sys; print(sys.version_info.minor)" 2>/dev/null || echo 0)
    if [[ "$_major" -ne 3 || "$_minor" -lt 9 ]]; then continue; fi

    # Kiểm tra pip hoạt động (một số Python bị hỏng pip do libexpat mismatch)
    if ! "$candidate" -m pip --version &>/dev/null 2>&1; then
        echo -e "${YELLOW}⚠  $candidate có pip bị lỗi, thử Python khác...${RESET}"
        continue
    fi

    PYTHON="$candidate"
    break
done

if [[ -z "$PYTHON" ]]; then
    echo -e "${RED}❌ Không tìm thấy Python 3.9+ với pip hoạt động.${RESET}"
    echo ""
    echo "   Gợi ý: cài uv (trình cài đặt không dùng pip) rồi chạy lại:"
    echo "   curl -LsSf https://astral.sh/uv/install.sh | sh"
    echo "   source \$HOME/.local/bin/env"
    echo "   bash <(curl -sSL https://raw.githubusercontent.com/minhdo01011990-glitch/keyword-labeler/main/install.sh)"
    echo ""
    echo "   Hoặc sửa pip: brew reinstall python@3.12"
    exit 1
fi

echo -e "${BOLD}Python:${RESET} $("$PYTHON" --version)"

echo -e "${BOLD}Cài đặt keyword-labeler từ PyPI...${RESET}"
"$PYTHON" -m pip install --quiet --upgrade keyword-labeler

echo -e "${BOLD}Chạy installer...${RESET}"
"$("$PYTHON" -c "import sysconfig; print(sysconfig.get_path('scripts'))")/keyword-labeler-install" 2>/dev/null \
    || "$("$PYTHON" -m site --user-base 2>/dev/null)/bin/keyword-labeler-install" 2>/dev/null \
    || keyword-labeler-install
