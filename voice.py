"""Voice in and out.
STT: microphone -> Google Web Speech via speech_recognition (needs internet), Russian and English.
TTS: Windows SAPI voices via pyttsx3 (offline), spoken from a background thread so nothing waits for it.
Audio libraries are imported inside the functions: a machine without them still runs the text workflow."""
import array
import logging
import os
import re
import threading
import winsound

log = logging.getLogger("jarvis.voice")

LANGS = [x.strip() for x in os.getenv("JARVIS_VOICE_LANGS", "ru-RU,en-US").split(",") if x.strip()]
TTS = os.getenv("JARVIS_TTS", "1") != "0"  # JARVIS_TTS=0 keeps Jarvis silent
RATE = 16000
BLOCK = RATE // 10  # the recorder works in 0.1 s blocks
MAX_SECONDS = 15  # hard cap on one voice command
START_TIMEOUT = 5  # nobody spoke for this long after the beep: give up
SILENCE_SECONDS = 1.2  # this much quiet after speech ends the recording
# Loudness (RMS of 16-bit samples) that counts as speech. Microphones differ a lot, so the real threshold is
# max(MIN_THRESHOLD, NOISE_FACTOR x the room noise measured right before the beep). Raise JARVIS_MIC_THRESHOLD
# if recording never stops in a noisy room; lower it if a quiet voice is not picked up.
MIN_THRESHOLD = int(os.getenv("JARVIS_MIC_THRESHOLD", "500"))
NOISE_FACTOR = 2.5
SPEAK_MAX_CHARS = 300  # the spoken result is a summary, not a reading of the whole notice

_speaking = threading.Lock()  # one utterance at a time; SAPI engines must not overlap


def _rms(block: bytes) -> float:
    samples = array.array("h", block)
    return (sum(s * s for s in samples) / len(samples)) ** 0.5 if samples else 0.0


def _collect(blocks, threshold: float) -> bytes:
    """Consumes 0.1 s blocks until the speaker stops. Returns the audio, or b"" if nobody spoke."""
    frames, spoke, quiet = [], False, 0
    for i, block in enumerate(blocks):
        frames.append(block)
        if _rms(block) >= threshold:
            spoke, quiet = True, 0
        else:
            quiet += 1
        if spoke and quiet >= SILENCE_SECONDS * 10:
            break
        if not spoke and i + 1 >= START_TIMEOUT * 10:
            return b""
    return b"".join(frames) if spoke else b""


def record() -> bytes:
    """Beeps, then records one spoken command from the default microphone (16 kHz mono, 16-bit)."""
    import sounddevice as sd

    with sd.RawInputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=BLOCK) as stream:
        read = lambda: bytes(stream.read(BLOCK)[0])
        warm = [_rms(read()) for _ in range(5)]  # 0.5 s: the device's switch-on click, then the room noise
        threshold = max(MIN_THRESHOLD, NOISE_FACTOR * max(warm[2:]))
        winsound.Beep(880, 150)  # "speak now"
        for _ in range(3):  # drop the beep and its echo so they are not mistaken for speech
            read()
        log.info("Listening (speech threshold %.0f)...", threshold)
        pcm = _collect((read() for _ in range(MAX_SECONDS * 10)), threshold)
    winsound.Beep(660, 100)  # "got it"
    return pcm


def recognize(pcm: bytes) -> str:
    """Tries every language in LANGS and keeps the transcript the service is most confident about.
    ponytail: confidence comparison is a heuristic for 'which language was spoken'; if it picks wrong for you,
    set JARVIS_VOICE_LANGS to the single language you use."""
    import speech_recognition as sr

    recognizer, audio = sr.Recognizer(), sr.AudioData(pcm, RATE, 2)
    best, best_conf = "", -1.0
    for lang in LANGS:
        try:
            result = recognizer.recognize_google(audio, language=lang, show_all=True)
        except sr.UnknownValueError:  # this language made no sense of the audio: normal for the "other" language
            continue
        except sr.RequestError as e:
            raise RuntimeError(f"speech recognition service is unavailable: {e}") from e
        alternatives = result.get("alternative", []) if isinstance(result, dict) else []
        if alternatives:
            conf = float(alternatives[0].get("confidence", 0.0))
            log.info("Heard (%s, confidence %.2f): %s", lang, conf, alternatives[0]["transcript"])
            if conf > best_conf:
                best, best_conf = alternatives[0]["transcript"], conf
    return best.strip()


def listen() -> str:
    """One voice command as text; "" if nothing was said or understood."""
    pcm = record()
    return recognize(pcm) if pcm else ""


def _pick_voice(voices, text: str):
    """Russian voice for Cyrillic text (Kazakh included), English otherwise; None = keep the system default."""
    hints = ("ru-ru", "russian") if re.search("[Ѐ-ӿ]", text) else ("en-us", "en-gb", "english")
    for v in voices:
        if any(h in f"{v.id} {v.name}".lower() for h in hints):
            return v.id
    return None


def _say(text: str) -> None:
    try:
        with _speaking:
            import pythoncom
            import pyttsx3

            pythoncom.CoInitialize()  # SAPI is COM, and COM must be initialised in every thread that uses it
            try:
                engine = pyttsx3.init()
                voice = _pick_voice(engine.getProperty("voices"), text)
                if voice:
                    engine.setProperty("voice", voice)
                engine.say(text)
                engine.runAndWait()
                engine.stop()
            finally:
                pythoncom.CoUninitialize()
    except Exception:  # speech is a nicety: never let it take the agent down
        log.exception("speech output failed")


def selfcheck() -> str:
    """Silent report on the audio stack (`Jarvis.exe --voice-check` writes it to jarvis.log): shows at a glance
    whether the microphone, the recognizer's FLAC encoder and the speech voices made it into the build."""
    lines = []
    for name, probe in (
        ("microphone", lambda: __import__("sounddevice").query_devices(kind="input")["name"]),
        ("flac encoder", lambda: __import__("speech_recognition").audio.get_flac_converter()),
        ("voices", lambda: [v.name for v in __import__("pyttsx3").init().getProperty("voices")]),
    ):
        try:
            lines.append(f"{name}: OK ({probe()})")
        except Exception as e:
            lines.append(f"{name}: FAILED ({type(e).__name__}: {e})")
    return "; ".join(lines)


def speak(text: str) -> threading.Thread | None:
    """Starts speaking in the background and returns at once. Returns the thread (None if there is nothing to say)."""
    text = " ".join(text.split())[:SPEAK_MAX_CHARS]
    if not TTS or not text:
        return None
    thread = threading.Thread(target=_say, args=(text,), daemon=True, name="tts")
    thread.start()
    return thread
