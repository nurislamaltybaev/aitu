"""Start-up model picker and its wiring in main, plus tolerant parsing of Gemini-style actions.
The picker window is built for real but driven by a stand-in for gui._show, so nothing waits for a human."""
import os
import unittest
from tkinter import ttk
from unittest import mock

import gui
import main
import vision_client

MODELS = ["openai/gpt-6-luna", "anthropic/claude-haiku-5.5", "google/gemini-2.5-flash"]


def drive(action):
    """Replacement for gui._show: runs `action(root, combobox, ok_button)` instead of a blocking mainloop."""
    def show(root, focus):
        root.update()
        box = next(w for w in root.winfo_children() if isinstance(w, ttk.Combobox))
        ok = next(w for w in root.winfo_children() if isinstance(w, ttk.Button))
        action(root, box, ok)
    return show


class TestSelectModel(unittest.TestCase):
    def pick(self, action, default="openai/gpt-6-luna"):
        with mock.patch.object(gui, "_show", drive(action)):
            return gui.select_model(MODELS, default)

    def test_ok_without_touching_returns_default(self):
        self.assertEqual(self.pick(lambda root, box, ok: ok.invoke()), "openai/gpt-6-luna")

    def test_default_is_preselected_and_all_models_are_offered(self):
        seen = {}

        def look(root, box, ok):
            seen.update(value=box.get(), values=list(box.cget("values")))
            root.destroy()

        self.pick(look, default="anthropic/claude-haiku-5.5")
        self.assertEqual(seen, {"value": "anthropic/claude-haiku-5.5", "values": MODELS})

    def test_choosing_another_model_returns_it(self):
        def choose(root, box, ok):
            box.current(2)
            ok.invoke()

        self.assertEqual(self.pick(choose), "google/gemini-2.5-flash")

    def test_closing_the_window_returns_default(self):
        self.assertEqual(self.pick(lambda root, box, ok: root.destroy(), default="anthropic/claude-haiku-5.5"),
                         "anthropic/claude-haiku-5.5")

    def test_early_enter_is_ignored(self):
        def early_enter(root, box, ok):
            box.current(1)
            root.event_generate("<Return>")
            root.update()
            self.assertTrue(root.winfo_exists())
            root.destroy()

        self.assertEqual(self.pick(early_enter), "openai/gpt-6-luna")


class TestStartupWiring(unittest.TestCase):
    def start(self, env_model, picked):
        """Runs main.main() with the loop already stopped; returns (select_model mock, model used afterwards)."""
        env = {"JARVIS_MODEL": env_model} if env_model else {}
        with mock.patch.dict(os.environ, env), \
             mock.patch.object(main, "preflight", return_value=True), \
             mock.patch.object(main.gui, "select_model", return_value=picked) as select, \
             mock.patch.object(main.gui, "notify"), \
             mock.patch.object(main.keyboard, "add_hotkey"), \
             mock.patch.object(main.logging, "basicConfig"):
            if not env_model:
                os.environ.pop("JARVIS_MODEL", None)
            main.stop.set()
            try:
                main.main()
            finally:
                main.stop.clear()
            return select, vision_client.current_model()

    def test_model_is_asked_once_and_used_for_requests(self):
        select, used = self.start("openai/gpt-6-luna", "anthropic/claude-haiku-5.5")
        select.assert_called_once_with(main.MODEL_CHOICES, "openai/gpt-6-luna")
        self.assertEqual(used, "anthropic/claude-haiku-5.5")

    def test_configured_model_outside_the_list_is_offered_first(self):
        select, used = self.start("qwen/some-other-model", "qwen/some-other-model")
        select.assert_called_once_with(["qwen/some-other-model"] + main.MODEL_CHOICES, "qwen/some-other-model")
        self.assertEqual(used, "qwen/some-other-model")

    def test_run_loop_never_opens_the_picker(self):
        import inspect
        self.assertNotIn("select_model", inspect.getsource(main._run) + inspect.getsource(main.handle))


class TestGeminiStyleActions(unittest.TestCase):
    def test_actions_sent_as_json_strings_are_accepted(self):
        plan = vision_client.Plan.model_validate(
            {"thought_process": "t", "actions": ['{"action":"click","x":900,"y":500}', {"action": "wait", "seconds": 1}]})
        self.assertEqual([(a.action, a.x) for a in plan.actions], [("click", 900), ("wait", None)])

    def test_null_entries_are_dropped(self):
        plan = vision_client.Plan.model_validate({"thought_process": "t", "actions": [None, {"action": "press", "key": "tab"}]})
        self.assertEqual(len(plan.actions), 1)

    def test_broken_string_action_is_still_rejected(self):
        with self.assertRaises(ValueError):
            vision_client.Plan.model_validate({"thought_process": "t", "actions": ["click here"]})

    def test_invalid_action_inside_string_is_still_rejected(self):
        with self.assertRaises(ValueError):
            vision_client.Plan.model_validate({"thought_process": "t", "actions": ['{"action":"click"}']})


if __name__ == "__main__":
    unittest.main()
