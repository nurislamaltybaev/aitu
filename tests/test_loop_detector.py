"""Loop detector in main._run: identical actions stop the run only when the model's notes also stand still.
No network, no windows, no real input."""
import unittest
from unittest import mock

import main
import vision_client
from vision_client import Action, Plan


class Shot:
    scale = 1


CLICK_B = [Action(action="click", x=775, y=257), Action(action="wait", seconds=2.0)]


def plan(memory: str = "", actions=None, **kw) -> Plan:
    return Plan(thought_process="t", summary="s", requires_approval=False, memory=memory,
                actions=CLICK_B if actions is None else actions, **kw)


class TestLoopDetector(unittest.TestCase):
    def run_plans(self, plans):
        """Returns (final notice text, is_error, how many plans were executed)."""
        it = iter(plans)
        notices = []
        saved = dict(vision_client.USAGE)
        try:
            with mock.patch.object(vision_client, "plan", lambda prompt, shot, history, memory="": next(it)), \
                 mock.patch.object(main.executor, "run") as run, \
                 mock.patch.object(main.voice, "speak"), \
                 mock.patch.object(main.gui, "notify", lambda m, error=False: notices.append((m, error))), \
                 mock.patch.object(main.screen_capture, "capture", lambda: Shot()), \
                 mock.patch.object(main.time, "sleep"):
                main.handle(Shot(), "do the quiz")
        finally:
            vision_client.USAGE.update(saved)
        self.assertEqual(len(notices), 1)
        return notices[0][0], notices[0][1], run.call_count

    def test_same_answer_button_on_new_questions_is_not_a_loop(self):
        """The log that exposed the bug: answer B at the same spot for questions 8, 9, 10 and beyond."""
        plans = [plan(f"Q1..Q{q - 1} answered. Q{q} -> B") for q in range(8, 14)] + [plan(done=True, actions=[])]
        text, error, executed = self.run_plans(plans)
        self.assertIn("Task completed", text)
        self.assertFalse(error)
        self.assertEqual(executed, 6)

    def test_same_actions_with_unchanged_notes_stop_on_the_third_plan(self):
        text, error, executed = self.run_plans([plan("Q1: field still empty")] * 10)
        self.assertIn("the same actions repeated 3 times", text)
        self.assertTrue(error)
        self.assertEqual(executed, 2)

    def test_same_actions_without_any_notes_still_stop(self):
        text, error, executed = self.run_plans([plan("")] * 10)
        self.assertIn("the same actions repeated 3 times", text)
        self.assertEqual(executed, 2)

    def test_empty_notes_reply_keeps_old_notes_and_counts_as_standing_still(self):
        # the model wrote notes once, then keeps answering with an empty memory field: nothing is moving
        text, error, executed = self.run_plans([plan("Q1 -> B")] + [plan("")] * 10)
        self.assertIn("the same actions repeated 3 times", text)
        self.assertEqual(executed, 2)

    def test_two_identical_steps_then_progress_resets_the_count(self):
        other = [Action(action="click", x=527, y=295)]
        plans = [plan("n"), plan("n"), plan("n", actions=other), plan("n"), plan("n"), plan(done=True, actions=[])]
        text, error, executed = self.run_plans(plans)
        self.assertIn("Task completed", text)
        self.assertEqual(executed, 5)


if __name__ == "__main__":
    unittest.main()
