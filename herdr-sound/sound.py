#!/usr/bin/env python3
"""Small, dependency-free macOS sound engine for a Herdr plugin."""

import argparse
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import wave

ROOT = Path(__file__).resolve().parent
CONFIG = Path(os.environ.get("HERDR_SOUND_CONFIG", "~/.config/herdr/ambient-sound.json")).expanduser()
STATE = Path(os.environ.get("HERDR_PLUGIN_STATE_DIR", "~/.local/state/herdr-ambient-sound")).expanduser()
SOUNDS = ("pulse", "tool", "done")
RATE = 22050


def settings():
    defaults = {"enabled": True, "volume": 0.35, "pulse": True, "tool": True, "done": True,
                "pulse_interval": 2.4, "tool_cooldown": 0.18}
    if CONFIG.exists():
        try:
            overrides = json.loads(CONFIG.read_text())
            if isinstance(overrides, dict):
                defaults.update(overrides)
        except (OSError, ValueError):
            pass
    for key, minimum, maximum, fallback in (("volume", 0.0, 1.0, 0.35),
                                             ("pulse_interval", 0.8, 60.0, 2.4),
                                             ("tool_cooldown", 0.0, 10.0, 0.18)):
        try:
            value = float(defaults[key])
            defaults[key] = max(minimum, min(maximum, value)) if math.isfinite(value) else fallback
        except (TypeError, ValueError):
            defaults[key] = fallback
    return defaults


def tone(t, frequency):
    return math.sin(2 * math.pi * frequency * t)


def sample(kind, t):
    if kind == "pulse":
        # A soft two-note breath, short enough to leave silence between pulses.
        if t >= 0.7:
            return 0.0
        envelope = math.sin(math.pi * t / 0.7) ** 2
        return 0.12 * envelope * (0.7 * tone(t, 130.8) + 0.3 * tone(t, 196))
    if kind == "tool":
        if t >= 0.25:
            return 0.0
        envelope = min(1.0, t / 0.006) * math.exp(-17 * t)
        return 0.20 * envelope * (0.75 * tone(t, 1046.5) + 0.25 * tone(t, 1568))
    if kind == "done":
        if t >= 0.8:
            return 0.0
        value = 0.0
        for start, frequency in ((0.0, 523.25), (0.16, 659.25), (0.32, 783.99)):
            delta = t - start
            if delta >= 0:
                envelope = min(1.0, delta / 0.014) * math.exp(-5.5 * delta)
                value += 0.11 * envelope * (tone(delta, frequency) + 0.18 * tone(delta, 2 * frequency))
        return value
    raise ValueError(kind)


def render(kind, destination):
    import struct
    duration = {"pulse": 0.7, "tool": 0.25, "done": 0.8}[kind]
    frames = bytearray()
    for index in range(int(duration * RATE)):
        value = max(-1.0, min(1.0, sample(kind, index / RATE)))
        frames.extend(struct.pack("<h", round(32767 * value)))
    with wave.open(str(destination), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(RATE)
        output.writeframes(frames)


def audio_path(kind):
    path = ROOT / "audio" / f"{kind}.wav"
    if not path.exists():
        path.parent.mkdir(exist_ok=True)
        render(kind, path)
    return path


def play(kind, config=None):
    config = config or settings()
    if not config["enabled"] or not config[kind] or config["volume"] <= 0:
        return False
    if sys.platform != "darwin":
        return False
    try:
        subprocess.Popen(["afplay", "-v", str(config["volume"]), str(audio_path(kind))],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False


def agent_list(socket_path):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(2.0)
        connection.connect(socket_path)
        connection.sendall(b'{"id":"sound","method":"agent.list","params":{}}\n')
        with connection.makefile("rb") as response:
            message = json.loads(response.readline())
    return message["result"]["agents"]


def working_states(agents):
    return {agent["pane_id"]: agent["agent_status"] for agent in agents
            if agent.get("agent") and agent.get("agent_status")}


def watch():
    socket_path = os.environ.get("HERDR_SOCKET_PATH")
    if not socket_path:
        return
    STATE.mkdir(parents=True, exist_ok=True)
    lock_name = hashlib.sha256(socket_path.encode()).hexdigest()[:16]
    with open(STATE / f"{lock_name}.lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        armed = set()
        try:
            event = json.loads(os.environ.get("HERDR_PLUGIN_EVENT_JSON", "{}"))
            data = event.get("data", {})
            if data.get("agent_status") == "working" and data.get("pane_id"):
                armed.add(data["pane_id"])
        except ValueError:
            pass
        quiet_since = None
        last_pulse = float("-inf")
        while True:
            try:
                states = working_states(agent_list(socket_path))
            except (OSError, ValueError, KeyError):
                return
            active = {pane for pane, status in states.items() if status == "working"}
            for pane in list(armed):
                status = states.get(pane)
                if status in ("idle", "done"):
                    play("done")
                    armed.remove(pane)
                elif status in ("blocked", None):
                    armed.remove(pane)
            armed.update(active)
            now = time.monotonic()
            if active:
                quiet_since = None
                config = settings()
                if now - last_pulse >= config["pulse_interval"]:
                    play("pulse", config)
                    last_pulse = now
            else:
                if quiet_since is None:
                    quiet_since = now
                elif now - quiet_since > 2.0:
                    return
            time.sleep(0.35)


def tool():
    # Agent hooks are installed at user level; only make noise inside Herdr.
    if os.environ.get("HERDR_ENV") != "1":
        return
    config = settings()
    if not config["enabled"] or not config["tool"]:
        return
    STATE.mkdir(parents=True, exist_ok=True)
    with open(STATE / "tool.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        stamp = STATE / "last-tool"
        now = time.monotonic()
        try:
            last = float(stamp.read_text())
        except (OSError, ValueError):
            last = 0.0
        if now - last >= config["tool_cooldown"] and play("tool", config):
            stamp.write_text(str(now))


def main():
    parser = argparse.ArgumentParser(description="Herdr Ambient Sound")
    parser.add_argument("command", choices=("watch", "tool", "preview", "render"))
    args = parser.parse_args()
    if args.command == "watch":
        watch()
    elif args.command == "tool":
        tool()
    elif args.command == "render":
        for kind in SOUNDS:
            audio_path(kind)
    else:
        for kind in SOUNDS:
            print(kind, flush=True)
            play(kind)
            time.sleep({"pulse": 1.0, "tool": 0.5, "done": 1.0}[kind])


if __name__ == "__main__":
    main()
