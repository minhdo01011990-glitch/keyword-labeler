import importlib.resources
import json
import os
import shutil
import sys
from pathlib import Path


def get_claude_desktop_config_path() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    elif sys.platform == "win32":
        appdata = os.environ.get("APPDATA", "")
        return Path(appdata) / "Claude" / "claude_desktop_config.json" if appdata else Path.home() / "AppData" / "Roaming" / "Claude" / "claude_desktop_config.json"
    else:
        return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"


def get_server_command() -> str:
    cmd = shutil.which("keyword-labeler-server")
    if cmd:
        return cmd
    # Fallback: run as module
    return f"{sys.executable} -m keyword_labeler.server"


def main():
    server_cmd = get_server_command()

    # --- Claude Desktop config ---
    config_path = get_claude_desktop_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)

    config = {}
    if config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            try:
                config = json.load(f)
            except json.JSONDecodeError:
                config = {}

    config.setdefault("mcpServers", {})
    config["mcpServers"]["keyword-labeler"] = {
        "command": server_cmd,
    }

    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

    print(f"  Claude Desktop config updated: {config_path}")

    # --- .mcp.json for Claude Code ---
    # KEYWORD_LABELER_PROJECT_DIR is set by install.sh so this works for both
    # editable and non-editable installs.
    project_dir_env = os.environ.get("KEYWORD_LABELER_PROJECT_DIR")
    if project_dir_env:
        mcp_json_path = Path(project_dir_env) / ".mcp.json"
    else:
        mcp_json_path = Path(__file__).parent.parent.parent / ".mcp.json"
    mcp_config = {
        "mcpServers": {
            "keyword-labeler": {
                "command": server_cmd,
            }
        }
    }
    with open(mcp_json_path, "w", encoding="utf-8") as f:
        json.dump(mcp_config, f, indent=2, ensure_ascii=False)

    print(f"  .mcp.json updated: {mcp_json_path}")

    # --- SKILL.md for Claude Code slash command ---
    skill_dst = Path.home() / ".claude" / "skills" / "keyword" / "SKILL.md"
    try:
        # Try to read from package data first (PyPI install)
        skill_src = Path(__file__).parent / "data" / "SKILL.md"
        if not skill_src.exists():
            # Fallback: project root (git clone install)
            project_root = Path(project_dir_env) if project_dir_env else Path(__file__).parent.parent.parent
            skill_src = project_root / "skills" / "keyword" / "SKILL.md"

        if skill_src.exists():
            skill_dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(skill_src, skill_dst)
            print(f"  SKILL.md installed: {skill_dst}")
        else:
            print("  SKILL.md not found — skipping Claude Code skill setup")
    except Exception as e:
        print(f"  Warning: could not install SKILL.md: {e}")


if __name__ == "__main__":
    main()
