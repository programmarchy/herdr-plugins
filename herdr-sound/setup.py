#!/usr/bin/env python3
"""Link this local Herdr plugin and add non-blocking tool hooks to both agents."""

import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
PLUGIN_ID = "programmarchy.ambient-sound"
COMMAND = ("sh -c 'if [ \"${HERDR_ENV:-}\" = 1 ]; then "
           "herdr plugin action invoke programmarchy.ambient-sound.tool >/dev/null 2>&1 || true; fi'")
MARKER = Path.home() / ".config/herdr/ambient-sound-install.json"


def back_up(path):
    backup = path.with_name(path.name + ".backup-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    shutil.copy2(path, backup)
    return backup


def is_our_hook(command):
    return command == COMMAND or (
        isinstance(command, str) and
        command.startswith("python3 ") and
        (command.endswith("/herdr-sound/sound.py' tool") or
         command.endswith("/herdr-sound/sound.py tool"))
    )


def add_hook(path, agent):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(path.read_text()) if path.exists() else {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object")
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError(f"{path}: hooks must be an object")
    groups = hooks.setdefault("PreToolUse", [])
    if not isinstance(groups, list):
        raise ValueError(f"{path}: PreToolUse must be an array")
    found = False
    migrated = False
    for group in groups:
        original_hooks = group.get("hooks", [])
        retained = []
        for hook in original_hooks:
            command = hook.get("command")
            if is_our_hook(command):
                if found:
                    migrated = True
                    continue
                found = True
                if command != COMMAND:
                    hook["command"] = COMMAND
                    migrated = True
            retained.append(hook)
        group["hooks"] = retained
    groups[:] = [group for group in groups if group.get("hooks")]
    if not found:
        groups.append({"hooks": [{"type": "command", "command": COMMAND, "async": True}]})
    elif not migrated:
        return False
    if path.exists():
        backup = back_up(path)
        print(f"Backed up {path} to {backup}")
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    print(f"{'Updated' if found else 'Added'} {agent} tool hook in {path}")
    return True


def remove_hook(path, agent):
    if not path.exists():
        return False
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or not isinstance(data.get("hooks"), dict):
        raise ValueError(f"{path}: expected a hooks object")
    groups = data["hooks"].get("PreToolUse", [])
    if not isinstance(groups, list):
        raise ValueError(f"{path}: PreToolUse must be an array")
    changed = False
    remaining = []
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("hooks", []), list):
            raise ValueError(f"{path}: invalid PreToolUse group")
        retained = [hook for hook in group.get("hooks", [])
                    if not isinstance(hook, dict) or not is_our_hook(hook.get("command"))]
        changed |= len(retained) != len(group.get("hooks", []))
        if retained:
            remaining.append({**group, "hooks": retained})
        elif not group.get("hooks"):
            remaining.append(group)
    if not changed:
        return False
    if remaining:
        data["hooks"]["PreToolUse"] = remaining
    else:
        data["hooks"].pop("PreToolUse", None)
    backup = back_up(path)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    print(f"Removed {agent} tool hook from {path} (backup: {backup})")
    return True


def config_section(text, name):
    header = re.search(rf"(?m)^\[{re.escape(name)}\][ \t]*(?:\r?\n|$)", text)
    if not header:
        return None
    next_header = re.search(r"(?m)^\[", text[header.end():])
    end = header.end() + next_header.start() if next_header else len(text)
    return text[header.start():end]


def sound_section(text):
    return config_section(text, "ui.sound")


def save_marker(changes):
    data = json.loads(MARKER.read_text()) if MARKER.exists() else {}
    data.update(changes)
    MARKER.parent.mkdir(parents=True, exist_ok=True)
    MARKER.write_text(json.dumps(data) + "\n")


def disable_native_sound(path):
    if not path.exists():
        return False
    current = path.read_text()
    before_section = sound_section(current)
    section = re.search(r"(?m)^\[ui\.sound\][ \t]*(?:\r?\n|$)", current)
    if section:
        rest = current[section.end():]
        next_section = re.search(r"(?m)^\[", rest)
        body_end = section.end() + (next_section.start() if next_section else len(rest))
        body = current[section.end():body_end]
        enabled = re.search(r"(?m)^[ \t]*enabled[ \t]*=[ \t]*(true|false)[ \t]*(?:#.*)?$", body)
        if enabled and enabled.group(1) == "false":
            return False
        if enabled:
            start = section.end() + enabled.start(1)
            updated = current[:start] + "false" + current[start + len(enabled.group(1)):]
        else:
            updated = current[:section.end()] + "enabled = false\n" + current[section.end():]
    else:
        updated = current.rstrip() + "\n\n[ui.sound]\nenabled = false\n"
    backup = back_up(path)
    path.write_text(updated)
    if not MARKER.exists() or "after_section" not in json.loads(MARKER.read_text()):
        save_marker({"before_section": before_section,
                     "after_section": sound_section(updated)})
    print(f"Disabled Herdr's built-in background sound to avoid double chimes (backup: {backup})")
    return True


def disable_desktop_notifications(path):
    if not path.exists():
        return False
    current = path.read_text()
    before_section = config_section(current, "ui.toast")
    if before_section:
        setting = re.search(r'(?m)^[ \t]*delivery[ \t]*=[ \t]*"([^"]+)"', before_section)
        if setting and setting.group(1) == "off":
            return False
        if setting:
            position = current.index(before_section) + setting.start(1)
            updated = current[:position] + "off" + current[position + len(setting.group(1)):]
        else:
            position = current.index(before_section) + before_section.index("\n") + 1
            updated = current[:position] + 'delivery = "off"\n' + current[position:]
    else:
        updated = current.rstrip() + '\n\n[ui.toast]\ndelivery = "off"\n'
    backup = back_up(path)
    path.write_text(updated)
    if not MARKER.exists() or "after_toast_section" not in json.loads(MARKER.read_text()):
        save_marker({"before_toast_section": before_section,
                     "after_toast_section": config_section(updated, "ui.toast")})
    print(f"Disabled Herdr desktop notifications (backup: {backup})")
    return True


def restore_native_sound(path):
    if not MARKER.exists() or not path.exists():
        return False
    marker = json.loads(MARKER.read_text())
    current = path.read_text()
    updated = current
    for label, name, before_key, after_key in (
        ("sound", "ui.sound", "before_section", "after_section"),
        ("notification", "ui.toast", "before_toast_section", "after_toast_section"),
    ):
        installed_section = marker.get(after_key)
        if not installed_section:
            continue
        if config_section(updated, name) == installed_section:
            updated = updated.replace(installed_section, marker.get(before_key) or "", 1)
            marker.pop(before_key, None)
            marker.pop(after_key, None)
        else:
            print(f"Herdr {label} settings changed since install; leaving them as they are.")
    if updated == current:
        return False
    backup = back_up(path)
    path.write_text(updated)
    if marker:
        MARKER.write_text(json.dumps(marker) + "\n")
    else:
        MARKER.unlink()
    print(f"Restored Herdr's previous sound and notification settings (backup: {backup})")
    return True


def main():
    parser = argparse.ArgumentParser(description="Install or uninstall Ambient Sound for Herdr")
    parser.add_argument("--skip-hooks", action="store_true")
    parser.add_argument("--keep-native-sound", action="store_true")
    parser.add_argument("--keep-desktop-notifications", action="store_true")
    parser.add_argument("--configure-only", action="store_true")
    parser.add_argument("--uninstall", action="store_true")
    args = parser.parse_args()
    if args.uninstall:
        unlinked = subprocess.run(["herdr", "plugin", "unlink", PLUGIN_ID],
                                  capture_output=True, text=True)
        if unlinked.returncode:
            subprocess.run(["herdr", "plugin", "uninstall", PLUGIN_ID], check=False)
        remove_hook(Path.home() / ".claude/settings.json", "Claude Code")
        remove_hook(Path.home() / ".codex/hooks.json", "Codex")
        if restore_native_sound(Path.home() / ".config/herdr/config.toml"):
            subprocess.run(["herdr", "server", "reload-config"], check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print("Uninstalled Ambient Sound. Restart Claude Code and Codex sessions to unload hooks.")
        return
    if not args.configure_only:
        subprocess.run(["herdr", "plugin", "link", str(ROOT)], check=True)
        # The first local prototype used this id. Avoid duplicate status hooks.
        subprocess.run(["herdr", "plugin", "unlink", "local.ambient-sound"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    if not args.skip_hooks:
        add_hook(Path.home() / ".claude/settings.json", "Claude Code")
        add_hook(Path.home() / ".codex/hooks.json", "Codex")
    config_path = Path.home() / ".config/herdr/config.toml"
    changed_config = False
    if not args.keep_native_sound:
        changed_config |= disable_native_sound(config_path)
    if not args.keep_desktop_notifications:
        changed_config |= disable_desktop_notifications(config_path)
    if changed_config:
        subprocess.run(["herdr", "server", "reload-config"], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("Configured Ambient Sound. Restart Claude Code and Codex sessions to load new hooks.")
    print("Codex may ask you to trust the new hook through /hooks.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(f"setup: {exc}", file=sys.stderr)
        sys.exit(1)
