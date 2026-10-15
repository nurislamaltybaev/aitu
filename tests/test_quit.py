"""Quit hotkey: registered at start-up and exits the process immediately."""
import os
import unittest
from unittest import mock

import main


class TestQuit(unittest.TestCase):
    def test_quit_now_exits_the_process_with_code_0(self):
        with mock.patch.object(main.os, "_exit") as exit_:
            main.quit_now()
        exit_.assert_called_once_with(0)

    def test_quit_hotkey_is_bound_to_quit_now(self):
        with mock.patch.dict(os.environ, {"JARVIS_MODEL": "openai/gpt-6-luna"}), \
             mock.patch.object(main, "preflight", return_value=True), \
             mock.patch.object(main.gui, "select_model", side_effect=lambda choices, default: default), \
             mock.patch.object(main.gui, "notify"), \
             mock.patch.object(main.keyboard, "add_hotkey") as add_hotkey, \
             mock.patch.object(main.logging, "basicConfig"):
            main.stop.set()  # skip the wait loop
            try:
                main.main()
            finally:
                main.stop.clear()
        bound = {call.args[0]: call.args[1] for call in add_hotkey.call_args_list}
        self.assertIs(bound[main.QUIT_HOTKEY], main.quit_now)


if __name__ == "__main__":
    unittest.main()
