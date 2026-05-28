"""keyword-labeler-install — configures Claude Desktop and Claude Code CLI."""
import json
import os
import platform
import shutil
import subprocess
import time
from pathlib import Path

_BOLD = "\033[1m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RESET = "\033[0m"
_DIV = "━" * 53


def _ok(msg: str) -> None:
    print(f"  {_BOLD}{_GREEN}✅ {msg}{_RESET}")


def _warn(msg: str) -> None:
    print(f"  {_YELLOW}⚠️  {msg}{_RESET}")


def _step(n: int, total: int, msg: str) -> None:
    print(f"\n{_BOLD}{n}/{total} {msg}{_RESET}")


def _get_desktop_config_path() -> Path:
    system = platform.system()
    if system == "Darwin":
        return Path.home() / "Library/Application Support/Claude/claude_desktop_config.json"
    if system == "Windows":
        appdata = os.environ.get("APPDATA", "")
        return (
            Path(appdata) / "Claude/claude_desktop_config.json"
            if appdata
            else Path.home() / "AppData/Roaming/Claude/claude_desktop_config.json"
        )
    return Path.home() / ".config/Claude/claude_desktop_config.json"


def _get_binary() -> str:
    return shutil.which("keyword-labeler-server") or "keyword-labeler-server"


def _configure_desktop(binary: str) -> None:
    config_path = _get_desktop_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)

    config: dict = {}
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    config.setdefault("mcpServers", {})["keyword-labeler"] = {"command": binary}
    config_path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    _ok("claude_desktop_config.json")


def _configure_claude_code(binary: str) -> None:
    settings_path = Path.home() / ".claude/settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)

    settings: dict = {}
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    settings.setdefault("mcpServers", {})["keyword-labeler"] = {"command": binary}
    settings_path.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    _ok("~/.claude/settings.json")


def _install_skill() -> None:
    skill_dst = Path.home() / ".claude/skills/keyword/SKILL.md"
    try:
        skill_src = Path(__file__).parent / "data" / "SKILL.md"
        if not skill_src.exists():
            project_root = Path(__file__).parent.parent.parent
            skill_src = project_root / "skills" / "keyword" / "SKILL.md"

        if skill_src.exists():
            skill_dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(skill_src, skill_dst)
            _ok(f"SKILL.md → {skill_dst}")
        else:
            _warn("SKILL.md not found — skipping Claude Code skill setup")
    except Exception as e:
        _warn(f"Could not install SKILL.md: {e}")


def _restart_claude() -> None:
    if platform.system() != "Darwin":
        _warn("Restart Claude Desktop thủ công để áp dụng thay đổi")
        return
    subprocess.run(["osascript", "-e", 'tell application "Claude" to quit'], capture_output=True)
    time.sleep(3)
    subprocess.run(["open", "-a", "Claude"], capture_output=True)
    _ok("Claude Desktop đã restart")


def main() -> None:
    print(f"\n{_BOLD}{_DIV}{_RESET}")
    print(f"{_BOLD}  Keyword Labeler — Install{_RESET}")
    print(f"{_BOLD}{_DIV}{_RESET}")

    binary = _get_binary()
    print(f"\n  Binary: {binary}")

    _step(1, 3, "Cấu hình Claude Desktop MCP server...")
    _configure_desktop(binary)

    _step(2, 3, "Cấu hình Claude Code CLI...")
    _configure_claude_code(binary)

    _step(3, 3, "Cài đặt SKILL.md cho lệnh /keyword...")
    _install_skill()

    _restart_claude()

    print(f"\n{_BOLD}{_GREEN}{_DIV}{_RESET}")
    print(f"{_BOLD}{_GREEN}  Cài đặt hoàn tất!{_RESET}")
    print(f"{_BOLD}{_GREEN}{_DIV}{_RESET}")
    print(f"\n  Claude Desktop → gõ /keyword để bắt đầu")
    print(f"  Claude Code CLI → gõ /keyword (sau khi mở terminal mới)")
    print(f"\n{_BOLD}{_GREEN}{_DIV}{_RESET}\n")


if __name__ == "__main__":
    main()
