"""Voice module and its wiring in main. No microphone, no speakers, no network: audio and services are faked."""
import array
import types
import unittest
from unittest import mock

import main
import voice

LOUD = array.array("h", [8000, -8000] * 800).tobytes()   # one 0.1 s block of "speech"
QUIET = array.array("h", [20, -20] * 800).tobytes()      # one 0.1 s block of room noise


class TestEndOfSpeech(unittest.TestCase):
    def test_stops_after_silence_following_speech(self):
        blocks = [QUIET] * 5 + [LOUD] * 10 + [QUIET] * 100
        pcm = voice._collect(iter(blocks), threshold=500)
        self.assertEqual(len(pcm), (5 + 10 + 12) * len(LOUD))  # 1.2 s of trailing silence, then stop

    def test_short_pause_inside_a_sentence_does_not_stop(self):
        blocks = [LOUD] * 5 + [QUIET] * 8 + [LOUD] * 5 + [QUIET] * 50
        pcm = voice._collect(iter(blocks), threshold=500)
        self.assertEqual(len(pcm), (5 + 8 + 5 + 12) * len(LOUD))

    def test_nobody_speaks_returns_empty_after_timeout(self):
        consumed = []
        blocks = (consumed.append(1) or QUIET for _ in range(300))
        self.assertEqual(voice._collect(blocks, threshold=500), b"")
        self.assertEqual(len(consumed), voice.START_TIMEOUT * 10)

    def test_speech_until_the_hard_cap_is_returned(self):
        self.assertEqual(len(voice._collect(iter([LOUD] * 150), threshold=500)), 150 * len(LOUD))

    def test_rms_of_empty_block_is_zero(self):
        self.assertEqual(voice._rms(b""), 0.0)


class TestRecognize(unittest.TestCase):
    def run_recognize(self, answers):
        """answers: {language: service reply}; an Exception instance is raised instead of returned."""
        fake_sr = types.SimpleNamespace(RequestError=type("RequestError", (Exception,), {}),
                                        UnknownValueError=type("UnknownValueError", (Exception,), {}))

        class Recognizer:
            def recognize_google(self, audio, language, show_all):
                reply = answers[language]
                if reply == "unknown":  # what the real library does when a language cannot make sense of the audio
                    raise fake_sr.UnknownValueError()
                if isinstance(reply, Exception):
                    raise fake_sr.RequestError(str(reply))
                return reply

        fake_sr.Recognizer = Recognizer
        fake_sr.AudioData = lambda pcm, rate, width: (pcm, rate, width)
        with mock.patch.dict("sys.modules", {"speech_recognition": fake_sr}), \
             mock.patch.object(voice, "LANGS", ["ru-RU", "en-US"]):
            return voice.recognize(b"\x00\x00" * 100)

    def test_keeps_the_more_confident_language(self):
        ru = {"alternative": [{"transcript": "открой блокнот", "confidence": 0.93}]}
        en = {"alternative": [{"transcript": "at croy block note", "confidence": 0.41}]}
        self.assertEqual(self.run_recognize({"ru-RU": ru, "en-US": en}), "открой блокнот")
        en_good = {"alternative": [{"transcript": "open notepad", "confidence": 0.95}]}
        ru_bad = {"alternative": [{"transcript": "опен ноутпад", "confidence": 0.50}]}
        self.assertEqual(self.run_recognize({"ru-RU": ru_bad, "en-US": en_good}), "open notepad")

    def test_nothing_understood_returns_empty_string(self):
        self.assertEqual(self.run_recognize({"ru-RU": [], "en-US": []}), "")

    def test_only_one_language_understood(self):
        en = {"alternative": [{"transcript": "open notepad"}]}  # the service may omit the confidence
        self.assertEqual(self.run_recognize({"ru-RU": [], "en-US": en}), "open notepad")

    def test_language_that_cannot_parse_the_audio_is_skipped(self):
        ru = {"alternative": [{"transcript": "открой блокнот", "confidence": 0.93}]}
        self.assertEqual(self.run_recognize({"ru-RU": ru, "en-US": "unknown"}), "открой блокнот")
        self.assertEqual(self.run_recognize({"ru-RU": "unknown", "en-US": "unknown"}), "")

    def test_service_failure_is_reported(self):
        with self.assertRaises(RuntimeError):
            self.run_recognize({"ru-RU": OSError("no internet"), "en-US": []})


class TestSpeak(unittest.TestCase):
    VOICES = [types.SimpleNamespace(id="HKLM/TTS_MS_EN-US_ZIRA_11.0", name="Microsoft Zira Desktop - English (United States)"),
              types.SimpleNamespace(id="HKLM/TTS_MS_RU-RU_IRINA_11.0", name="Microsoft Irina Desktop - Russian")]

    def test_voice_follows_the_language_of_the_text(self):
        self.assertIn("IRINA", voice._pick_voice(self.VOICES, "Задача выполнена"))
        self.assertIn("IRINA", voice._pick_voice(self.VOICES, "Тапсырма орындалды: қате жоқ"))
        self.assertIn("ZIRA", voice._pick_voice(self.VOICES, "Task completed"))

    def test_missing_voice_falls_back_to_system_default(self):
        self.assertIsNone(voice._pick_voice(self.VOICES[:1], "Задача выполнена"))

    def test_speak_returns_immediately_and_runs_in_a_thread(self):
        release = voice.threading.Event()
        said = []
        with mock.patch.object(voice, "TTS", True), \
             mock.patch.object(voice, "_say", lambda text: (release.wait(5), said.append(text))):
            thread = voice.speak("Task   completed:\nall good")
            self.assertTrue(thread.is_alive())  # speak() came back while the "speech" is still running
            release.set()
            thread.join(5)
        self.assertEqual(said, ["Task completed: all good"])

    def test_disabled_or_empty_says_nothing(self):
        with mock.patch.object(voice, "_say") as say:
            with mock.patch.object(voice, "TTS", False):
                self.assertIsNone(voice.speak("hello"))
            with mock.patch.object(voice, "TTS", True):
                self.assertIsNone(voice.speak("  \n "))
        say.assert_not_called()

    def test_long_text_is_cut(self):
        with mock.patch.object(voice, "TTS", True), mock.patch.object(voice, "_say") as say:
            voice.speak("x" * 1000).join(5)
        self.assertEqual(len(say.call_args[0][0]), voice.SPEAK_MAX_CHARS)

    def test_speech_engine_error_does_not_escape(self):
        with mock.patch.dict("sys.modules", {"pyttsx3": None}):  # the import inside _say fails
            voice._say("hello")


class TestMainWiring(unittest.TestCase):
    class Shot:
        scale = 1

    def test_voice_hotkey_queues_a_voice_job_and_typed_hotkey_a_typed_one(self):
        for by_voice in (True, False):
            main.busy.clear()
            with mock.patch.object(main.screen_capture, "capture", return_value="SHOT"):
                main.on_hotkey(True) if by_voice else main.on_hotkey()
            self.assertEqual(main.jobs.get_nowait(), (by_voice, "SHOT"))
        main.busy.clear()

    def test_spoken_command_runs_like_a_typed_one(self):
        with mock.patch.object(main.voice, "listen", return_value="открой блокнот"), \
             mock.patch.object(main, "handle") as handle, mock.patch.object(main.gui, "notify") as notify:
            main.handle_voice("SHOT")
        handle.assert_called_once_with("SHOT", "открой блокнот")
        notify.assert_not_called()

    def test_nothing_heard_shows_one_notice_and_runs_nothing(self):
        with mock.patch.object(main.voice, "listen", return_value=""), \
             mock.patch.object(main, "handle") as handle, mock.patch.object(main.gui, "notify") as notify:
            main.handle_voice("SHOT")
        handle.assert_not_called()
        self.assertEqual(notify.call_count, 1)

    def test_microphone_failure_is_reported_not_raised(self):
        with mock.patch.object(main.voice, "listen", side_effect=OSError("no input device")), \
             mock.patch.object(main, "handle") as handle, mock.patch.object(main.gui, "notify") as notify:
            main.handle_voice("SHOT")
        handle.assert_not_called()
        self.assertIn("no input device", notify.call_args[0][0])

    def test_result_is_spoken_once_without_the_usage_line(self):
        done = main.vision_client.Plan(thought_process="t", summary="all good", done=True)

        def fake_plan(prompt, shot, history, memory=""):
            main.vision_client.USAGE["requests"] += 1
            return done

        saved = dict(main.vision_client.USAGE)
        try:
            with mock.patch.object(main.vision_client, "plan", fake_plan), \
                 mock.patch.object(main.voice, "speak") as speak, mock.patch.object(main.gui, "notify") as notify:
                main.handle(self.Shot(), "do it")
        finally:
            main.vision_client.USAGE.update(saved)
        speak.assert_called_once_with("Task completed:\nall good")
        self.assertIn("1 requests", notify.call_args[0][0])


if __name__ == "__main__":
    unittest.main()
