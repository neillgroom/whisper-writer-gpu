import os
import sys
from pathlib import Path
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from control_broker import Broker, ControlError, IntentParser, WindowsController, strip_wake_word


class IntentParserTests(unittest.TestCase):
    def setUp(self):
        self.parser = IntentParser()

    def test_wake_word_is_removed(self):
        self.assertEqual(strip_wake_word("Helene, open VS Code"), ("open VS Code", True))

    def test_open_project(self):
        self.assertEqual(
            self.parser.parse("open project Floodstream"),
            ("open_project", {"project": "Floodstream"}),
        )

    def test_shortcut(self):
        self.assertEqual(
            self.parser.parse("press ctrl+shift+p"),
            ("press_shortcut", {"shortcut": "ctrl+shift+p"}),
        )

    def test_spoken_shortcut(self):
        self.assertEqual(
            self.parser.parse("press control shift P"),
            ("press_shortcut", {"shortcut": "ctrl+shift+p"}),
        )

    def test_agent_task_is_preserved(self):
        operation, arguments = self.parser.parse(
            "start Claude Code in Floodstream and ask it to fix the failing test"
        )
        self.assertEqual(operation, "start_agent")
        self.assertEqual(arguments["project"], "Floodstream")
        self.assertEqual(arguments["task"], "fix the failing test")

    def test_four_way(self):
        self.assertEqual(
            self.parser.parse("run four way review on Floodstream"),
            (
                "run_workflow",
                {"workflow": "four way review", "project": "Floodstream"},
            ),
        )

    def test_arbitrary_shell_is_rejected(self):
        with self.assertRaises(ControlError):
            self.parser.parse("run remove everything")


class DispatchTests(unittest.TestCase):
    def test_route_dispatches_one_permitted_tool(self):
        broker = Broker({"speak_responses": False})
        broker.controller = mock.Mock(spec=WindowsController)
        broker.controller.open_app.return_value = "Opened VS Code"
        result = broker.dispatch("route", {"text": "open VS Code"})
        broker.controller.open_app.assert_called_once_with(app="VS Code")
        self.assertEqual(result["message"], "Opened VS Code")

    @mock.patch("control_broker.subprocess.Popen")
    @mock.patch("control_broker.os.name", "nt")
    def test_four_way_uses_real_shepherd_gate(self, popen):
        controller = WindowsController(
            {
                "factory": {
                    "root": r"C:\Projects\shepherd-factory",
                    "gate_command": ["pnpm", "tsx", "src/cli.ts", "gate"],
                    "base": "main",
                    "intent": "Voice-requested four-way review",
                }
            }
        )
        controller.find_project = mock.Mock(return_value=Path(r"C:\Projects\floodstream"))
        result = controller.run_workflow("four way review", "Floodstream")
        command = popen.call_args.args[0][-1]
        self.assertIn("src/cli.ts gate", command)
        self.assertIn("--repo", command)
        self.assertIn("--intent", command)
        self.assertEqual(result, "Started four-way gate for floodstream")


if __name__ == "__main__":
    unittest.main()
