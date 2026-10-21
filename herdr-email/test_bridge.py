import tempfile
import unittest
from unittest.mock import patch

import bridge


CONFIG = {"inbox_id": "agent@agentmail.to", "allowed_senders": ["me@gmail.com"], "api_key": "test"}


def message(**changes):
    result = {
        "inbox_id": "agent@agentmail.to",
        "message_id": "msg-1",
        "from": "Me <me@gmail.com>",
        "to": ["agent@agentmail.to"],
        "subject": "Fwd: [herdr:Work/Build] Please review",
        "text": "My note\n\nForwarded material",
        "labels": ["received"],
        "authentication_results": {"dmarc": "pass"},
    }
    result.update(changes)
    return result


SNAPSHOT = {
    "workspaces": [{"workspace_id": "w1", "label": "Work"}],
    "tabs": [{"workspace_id": "w1", "tab_id": "w1:t1", "label": "Build"}],
    "agents": [{"tab_id": "w1:t1", "pane_id": "w1:p1", "agent_status": "idle"}],
}


class BridgeTests(unittest.TestCase):
    def test_only_exact_authenticated_sender(self):
        self.assertEqual(bridge.check_sender(message(), CONFIG), (True, "me@gmail.com"))
        for changed in (
            {"from": "Me <other@gmail.com>"},
            {"from": "Me <me@gmail.com.evil.test>"},
            {"authentication_results": {"dmarc": "fail"}},
            {"authentication_results": None},
            {"labels": ["spam"]},
        ):
            with self.subTest(changed=changed):
                self.assertFalse(bridge.check_sender(message(**changed), CONFIG)[0])

    def test_subject_and_optional_plus_route(self):
        self.assertEqual(bridge.route_from_message(message(), CONFIG["inbox_id"]), ("Work", "Build"))
        plus = message(subject="Forwarded note", to=["Agent <agent+work.build@agentmail.to>"])
        self.assertEqual(bridge.route_from_message(plus, CONFIG["inbox_id"]), ("work", "build"))
        self.assertIsNone(bridge.route_from_message(message(subject="Forwarded note"), CONFIG["inbox_id"]))

    def test_resolve_requires_one_ready_agent(self):
        self.assertEqual(bridge.resolve_agent(SNAPSHOT, ("work", "build")), ("w1:p1", "ready"))
        busy = {**SNAPSHOT, "agents": [{**SNAPSHOT["agents"][0], "agent_status": "blocked"}]}
        self.assertIsNone(bridge.resolve_agent(busy, ("work", "build"))[0])
        ambiguous = {**SNAPSHOT, "agents": SNAPSHOT["agents"] * 2}
        self.assertIsNone(bridge.resolve_agent(ambiguous, ("work", "build"))[0])

    def test_dispatch_never_prompts_unauthorized_mail_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"HERDR_PLUGIN_STATE_DIR": directory}):
            db = bridge.open_db()
            bridge.record(db, "agent@agentmail.to", "msg-1")
            bridge.record(db, "agent@agentmail.to", "msg-1")
            with patch.object(bridge, "get_message", return_value=message(**{"from": "other@gmail.com"})), patch.object(bridge, "herdr_snapshot") as snapshot, patch.object(bridge.subprocess, "run") as prompt:
                bridge.dispatch(db, CONFIG)
                snapshot.assert_not_called()
                prompt.assert_not_called()
            self.assertEqual(db.execute("SELECT status FROM mail").fetchone()[0], "ignored")

    def test_dispatch_marks_sent_or_uncertain(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"HERDR_PLUGIN_STATE_DIR": directory}):
            db = bridge.open_db()
            bridge.record(db, "agent@agentmail.to", "msg-1")
            with patch.object(bridge, "get_message", return_value=message()), patch.object(bridge, "herdr_snapshot", return_value=SNAPSHOT), patch.object(bridge.subprocess, "run") as prompt:
                bridge.dispatch(db, CONFIG)
                self.assertEqual(prompt.call_args.args[0][:4], ["herdr", "agent", "prompt", "w1:p1"])
            self.assertEqual(db.execute("SELECT status FROM mail").fetchone()[0], "sent")
            bridge.set_status(db, "agent@agentmail.to", "msg-1", "queued", "")
            with patch.object(bridge, "get_message", return_value=message()), patch.object(bridge, "herdr_snapshot", return_value=SNAPSHOT), patch.object(bridge.subprocess, "run", side_effect=TimeoutError):
                bridge.dispatch(db, CONFIG)
            self.assertEqual(db.execute("SELECT status FROM mail").fetchone()[0], "uncertain")

    def test_html_forward_is_readable(self):
        self.assertIn("Forwarded material", bridge.body_text(message(text=None, html="<p>Forwarded <b>material</b></p><script>bad()</script>")))
        self.assertNotIn("bad()", bridge.body_text(message(text=None, html="<script>bad()</script>")))


if __name__ == "__main__":
    unittest.main()
