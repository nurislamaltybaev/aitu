"""Cost accounting / cost cap in main, and layout-independent key codes in executor.
No network, no windows, no real input, no .env."""
import types
import unittest
from unittest import mock

import pyautogui

import executor
import main
import vision_client
from server import planner
from vision_client import Action, Plan


class Shot:
    scale = 1


def step(i: int) -> Plan:
    return Plan(thought_process="t", summary=f"s{i}", requires_approval=False, actions=[Action(action="click", x=i + 1, y=5)])


class TestKeyCodes(unittest.TestCase):
    def test_latin_letters_and_digits_map_to_fixed_virtual_keys(self):
        km = pyautogui.platformModule.keyboardMapping
        self.assertEqual((km["v"], km["a"], km["l"], km["z"], km["0"], km["9"]), (0x56, 0x41, 0x4C, 0x5A, 0x30, 0x39))

    def test_named_keys_are_left_alone(self):
        self.assertEqual(pyautogui.platformModule.keyboardMapping["enter"], 0x0D)

    def test_type_pastes_with_ctrl_v_and_restores_clipboard(self):
        clip = {"v": "OLD"}
        with mock.patch.object(executor.pyperclip, "paste", lambda: clip["v"]), \
             mock.patch.object(executor.pyperclip, "copy", lambda t: clip.update(v=t)), \
             mock.patch.object(executor.pyautogui, "hotkey") as hotkey, \
             mock.patch.object(executor.time, "sleep"):
            hotkey.side_effect = lambda *k: self.assertEqual(clip["v"], "Сәлем abc")
            executor._type("Сәлем abc")
        hotkey.assert_called_once_with("ctrl", "v")
        self.assertEqual(clip["v"], "OLD")


class TestUsageCounter(unittest.TestCase):
    def setUp(self):
        self.saved = dict(planner.USAGE)
        planner.USAGE.update(requests=0, input=0, output=0, cost=0.0)

    def tearDown(self):
        planner.USAGE.update(self.saved)

    def reply(self, cost):
        usage = types.SimpleNamespace(prompt_tokens=100, completion_tokens=20, model_dump=lambda: {"cost": cost})
        return types.SimpleNamespace(usage=usage)

    def test_counts_tokens_and_cost(self):
        planner._count(self.reply(0.001))
        planner._count(self.reply(0.002))
        self.assertEqual((planner.USAGE["requests"], planner.USAGE["input"], planner.USAGE["output"]), (2, 200, 40))
        self.assertAlmostEqual(planner.USAGE["cost"], 0.003)

    def test_missing_usage_or_cost_does_not_crash(self):
        planner._count(types.SimpleNamespace(usage=None))
        planner._count(self.reply(None))
        self.assertEqual(planner.USAGE["requests"], 1)
        self.assertEqual(planner.USAGE["cost"], 0)


class TestCostCap(unittest.TestCase):
    def run_handle(self, plans, cost_per_request, max_cost):
        notices = []
        it = iter(plans)

        def fake_plan(prompt, shot, history, memory=""):
            vision_client.USAGE["requests"] += 1
            vision_client.USAGE["input"] += 1000
            vision_client.USAGE["cost"] += cost_per_request
            return next(it)

        saved = dict(vision_client.USAGE)
        try:
            with mock.patch.object(vision_client, "plan", fake_plan), \
                 mock.patch.object(main.executor, "run") as run, \
                 mock.patch.object(main.gui, "ask", lambda: "do it"), \
                 mock.patch.object(main.voice, "speak"), \
                 mock.patch.object(main.gui, "notify", lambda m, error=False: notices.append((m, error))), \
                 mock.patch.object(main.screen_capture, "capture", lambda: Shot()), \
                 mock.patch.object(main.time, "sleep"), \
                 mock.patch.object(main, "MAX_COST", max_cost):
                main.handle(Shot())
            return notices, run.call_count
        finally:
            vision_client.USAGE.update(saved)

    def test_run_stops_when_cost_limit_is_reached(self):
        notices, executed = self.run_handle((step(i) for i in range(50)), cost_per_request=0.01, max_cost=0.03)
        self.assertEqual(executed, 3)
        self.assertEqual(len(notices), 1)
        self.assertTrue(notices[0][1])
        self.assertIn("cost limit of $0.03", notices[0][0])

    def test_cheap_run_finishes_and_reports_usage_once(self):
        done = Plan(thought_process="t", summary="all good", done=True)
        notices, executed = self.run_handle([step(0), step(1), done], cost_per_request=0.0003, max_cost=0.25)
        self.assertEqual(executed, 2)
        self.assertEqual(len(notices), 1)
        self.assertFalse(notices[0][1])
        self.assertIn("Task completed", notices[0][0])
        self.assertIn("3 requests, 3000 tokens, $0.0009", notices[0][0])

    def test_usage_is_reset_for_every_command(self):
        done = Plan(thought_process="t", summary="ok", done=True)
        self.run_handle([step(0), done], 0.01, 0.25)
        notices, _ = self.run_handle([done], 0.01, 0.25)
        self.assertIn("1 requests", notices[0][0])


if __name__ == "__main__":
    unittest.main()
