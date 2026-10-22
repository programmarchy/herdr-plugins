import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import wave

import setup
import sound


class SoundTests(unittest.TestCase):
    def test_mute_actions_preserve_other_settings_and_toggle(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sound.json"
            path.write_text(json.dumps({"volume": 0.2, "tool": False}))
            with mock.patch.object(sound, "CONFIG", path), contextlib.redirect_stdout(io.StringIO()):
                sound.set_enabled("mute")
                self.assertFalse(sound.settings()["enabled"])
                sound.set_enabled("mute")
                sound.set_enabled("toggle")
                self.assertTrue(sound.settings()["enabled"])
                sound.set_enabled("unmute")
            self.assertEqual(json.loads(path.read_text()), {"volume": 0.2, "tool": False, "enabled": True})

    def test_rendered_audio_is_valid_and_decays(self):
        with tempfile.TemporaryDirectory() as directory:
            for kind in sound.SOUNDS:
                path = Path(directory) / f"{kind}.wav"
                sound.render(kind, path)
                with wave.open(str(path)) as reader:
                    self.assertEqual(reader.getframerate(), sound.RATE)
                    self.assertEqual(reader.getnchannels(), 1)
                    self.assertGreater(reader.getnframes(), 1000)
                    frames = reader.readframes(reader.getnframes())
                    self.assertNotEqual(frames, bytes(len(frames)))
                    self.assertLess(max(abs(int.from_bytes(frames[i:i+2], "little", signed=True))
                                        for i in range(len(frames)-2200, len(frames), 2)), 5000)

    def test_watch_plays_one_pulse_and_one_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            sequence = [
                [{"pane_id": "w1:p1", "agent": "codex", "agent_status": "working"}],
                [{"pane_id": "w1:p1", "agent": "codex", "agent_status": "working"}],
                [{"pane_id": "w1:p1", "agent": "codex", "agent_status": "done"}],
            ]
            plays = []
            clock = [0.0]

            def fake_agents(_):
                return sequence.pop(0) if sequence else [{"pane_id": "w1:p1", "agent": "codex", "agent_status": "idle"}]

            def tick(seconds):
                clock[0] += seconds

            with mock.patch.object(sound, "STATE", Path(directory)), \
                 mock.patch.dict(os.environ, {"HERDR_SOCKET_PATH": "/test/socket", "HERDR_PLUGIN_EVENT_JSON": "{}"}), \
                 mock.patch.object(sound, "agent_list", side_effect=fake_agents), \
                 mock.patch.object(sound, "play", side_effect=lambda kind, config=None: plays.append(kind)), \
                 mock.patch.object(sound.time, "monotonic", side_effect=lambda: clock[0]), \
                 mock.patch.object(sound.time, "sleep", side_effect=tick):
                sound.watch()
            self.assertEqual(plays.count("pulse"), 1)
            self.assertEqual(plays.count("done"), 1)

    def test_setup_preserves_existing_hooks_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hooks.json"
            original = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "true"}]}]}}
            path.write_text(json.dumps(original))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertTrue(setup.add_hook(path, "Codex"))
                self.assertFalse(setup.add_hook(path, "Codex"))
            installed = json.loads(path.read_text())
            self.assertEqual(installed["hooks"]["Stop"], original["hooks"]["Stop"])
            self.assertEqual(len(installed["hooks"]["PreToolUse"]), 1)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertTrue(setup.remove_hook(path, "Codex"))
                self.assertFalse(setup.remove_hook(path, "Codex"))
            self.assertEqual(json.loads(path.read_text()), original)
            self.assertEqual(len(list(Path(directory).glob("hooks.json.backup-*"))), 2)

    def test_local_hook_migrates_to_stable_plugin_action(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hooks.json"
            path.write_text(json.dumps({"hooks": {"PreToolUse": [{"hooks": [
                {"type": "command", "command": "python3 '/Users/example/herdr-sound/sound.py' tool", "async": True}
            ]}]}}))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertTrue(setup.add_hook(path, "Codex"))
            hooks = json.loads(path.read_text())["hooks"]["PreToolUse"]
            self.assertEqual(len(hooks), 1)
            self.assertEqual(hooks[0]["hooks"][0]["command"], setup.COMMAND)

    def test_setup_removes_duplicate_legacy_hook(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hooks.json"
            path.write_text(json.dumps({"hooks": {"PreToolUse": [
                {"hooks": [{"type": "command", "command": "python3 /Users/example/herdr-sound/sound.py tool"}]},
                {"hooks": [{"type": "command", "command": setup.COMMAND}]}
            ]}}))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertTrue(setup.add_hook(path, "Codex"))
            hooks = json.loads(path.read_text())["hooks"]["PreToolUse"]
            matches = [h for group in hooks for h in group["hooks"] if setup.is_our_hook(h["command"])]
            self.assertEqual(len(matches), 1)

    def test_disabling_native_sound_preserves_other_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text('[ui.sound]\nenabled = true\ndone_path = "my-tone.mp3"\n[ui.toast]\ndelivery = "off"\n')
            with contextlib.redirect_stdout(io.StringIO()), \
                 mock.patch.object(setup, "MARKER", Path(directory) / "install.json"):
                self.assertTrue(setup.disable_native_sound(path))
                self.assertFalse(setup.disable_native_sound(path))
                self.assertTrue(setup.restore_native_sound(path))
            result = path.read_text()
            self.assertIn("enabled = true", result)
            self.assertIn('done_path = "my-tone.mp3"', result)
            self.assertIn('[ui.toast]\ndelivery = "off"', result)
            self.assertEqual(len(list(Path(directory).glob("config.toml.backup-*"))), 2)

    def test_desktop_notification_setting_restores_on_uninstall(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            original = '[ui.toast]\ndelivery = "system"\nposition = "top-right"\n\n[ui.sound]\nenabled = false\n'
            path.write_text(original)
            with contextlib.redirect_stdout(io.StringIO()), \
                 mock.patch.object(setup, "MARKER", Path(directory) / "install.json"):
                self.assertTrue(setup.disable_desktop_notifications(path))
                self.assertFalse(setup.disable_desktop_notifications(path))
                self.assertIn('delivery = "off"', path.read_text())
                self.assertTrue(setup.restore_native_sound(path))
            self.assertEqual(path.read_text(), original)
            self.assertEqual(len(list(Path(directory).glob("config.toml.backup-*"))), 2)


if __name__ == "__main__":
    unittest.main()
