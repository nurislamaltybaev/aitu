"""Seed tests so the project's test command has something to run. No network, no windows, no input, no .env."""
import unittest

import main
import vision_client
from vision_client import Action


class TestParse(unittest.TestCase):
    def test_missing_approval_flag_fails_closed(self):
        plan = vision_client.parse('{"thought_process": "t", "actions": [{"action": "click", "x": 1, "y": 2}]}')
        self.assertTrue(plan.requires_approval)

    def test_action_without_required_field_is_rejected(self):
        with self.assertRaises(ValueError):
            vision_client.parse('[{"action": "click"}]')


class TestSecurityGate(unittest.TestCase):
    def test_delete_key_forces_confirmation(self):
        self.assertIsNotNone(main.check_forced_approval([Action(action="press", key="Delete")]))

    def test_alt_f4_forces_confirmation(self):
        self.assertIsNotNone(main.check_forced_approval([Action(action="hotkey", keys=["alt", "f4"])]))

    def test_enter_and_plain_typing_pass(self):
        actions = [Action(action="type", text="hello"), Action(action="press", key="enter")]
        self.assertIsNone(main.check_forced_approval(actions))


if __name__ == "__main__":
    unittest.main()
