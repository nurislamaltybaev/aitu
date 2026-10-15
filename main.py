"""Jarvis: Ctrl+Alt+J -> screenshot -> ask -> LLM plan -> human approval -> execute."""
import config  # first: loads .env before any module reads its settings
import screen_capture  # then: sets DPI awareness before any window exists

import logging
import os
import queue
import sys
import threading
import time

import keyboard
import pyautogui

import executor
import gui
import ticktick
import vision_client
import voice

HOTKEY = "ctrl+alt+j"
VOICE_HOTKEY = "ctrl+alt+v"  # same as HOTKEY, but the command is spoken instead of typed
QUIT_HOTKEY = "ctrl+alt+q"  # the .exe has no console, so Ctrl+C is not available
MAX_STEPS = int(os.getenv("JARVIS_MAX_STEPS", "100"))
MAX_REPEATS = 3  # identical plans in a row before the run is declared stuck
MAX_COST = float(os.getenv("JARVIS_MAX_COST", "0.25"))  # USD per command; a stuck run stops here instead of draining credits
MODEL_CHOICES = ["openai/gpt-6-luna", "anthropic/claude-haiku-5.5", "google/gemini-2.5-flash"]  # offered at start-up
# keys that delete or close things: a human approves them no matter what the model's flag says.
# Enter is NOT here (it fired on almost every step); submitting is covered only by the model's requires_approval.
CRITICAL_KEYS = {"delete", "del", "backspace", "f4"}
WIN_KEYS = {"win", "winleft", "winright"}  # alone it only opens Start (not gated); in a combo it runs system shortcuts
log = logging.getLogger("jarvis")
jobs: queue.Queue = queue.Queue()
busy = threading.Event()
stop = threading.Event()


def on_hotkey(by_voice: bool = False) -> None:
    """Runs in the keyboard thread: grab the screen NOW, hand everything else to the main (Tk) thread."""
    if busy.is_set():
        return
    busy.set()
    try:
        jobs.put((by_voice, screen_capture.capture()))
    except Exception:
        log.exception("screen capture failed")
        busy.clear()


def quit_now() -> None:
    """Quit hotkey, runs in the keyboard thread. Exits at once, even in the middle of a task or while a Jarvis
    window is waiting: the main thread is busy then and would only notice a polite stop flag after the
    task ends. A command cut off mid-way may leave a half-typed answer or Jarvis's text on the clipboard."""
    log.info("Quit hotkey pressed: exiting now")
    for h in logging.getLogger().handlers:
        h.flush()
    os._exit(0)


def submit_task(prompt: str) -> bool:
    """Called from the TickTick thread: queue a command unless Jarvis is busy."""
    if busy.is_set():
        return False
    busy.set()
    jobs.put(prompt)
    return True


def check_forced_approval(actions: list) -> str | None:
    """Reason the step must be confirmed by a human regardless of plan.requires_approval, or None."""
    for a in actions:
        if a.action == "press" and a.key.lower() in CRITICAL_KEYS:
            return f"critical key: {a.key}"
        if a.action == "hotkey" and (hit := [k for k in a.keys if k.lower() in CRITICAL_KEYS | WIN_KEYS]):
            return f"critical hotkey: {'+'.join(a.keys)} (contains {', '.join(hit)})"
    return None


def handle(shot, prompt: str | None = None) -> None:
    """Runs one command silently, then shows exactly one notice with the outcome. The only window that can
    appear in between is gui.confirm() for steps the model flagged as risky."""
    prompt = prompt or gui.ask()
    if not prompt:
        return
    log.info("Command: %s", prompt)
    msg, error = _run(shot, prompt)
    voice.speak(msg)  # background thread: the notice below does not wait for it
    used = vision_client.USAGE
    if used["requests"]:
        msg += f"\n\n{used['requests']} requests, {used['input'] + used['output']} tokens, ${used['cost']:.4f}"
    log.log(logging.ERROR if error else logging.INFO, "Result: %s", msg)
    gui.notify(msg, error=error)


def handle_voice(shot) -> None:
    """Voice hotkey: listen for one spoken command, then run it exactly like a typed one."""
    try:
        prompt = voice.listen()
    except Exception as e:  # no microphone, no internet, audio libraries missing...
        log.exception("voice input failed")
        gui.notify(f"Voice input failed:\n{e}", error=True)
        return
    if not prompt:
        gui.notify("I did not hear a command. Press the voice hotkey and speak after the beep.", error=True)
        return
    handle(shot, prompt)


def _run(shot, prompt: str) -> tuple[str, bool]:
    """Closed loop: plan -> approve -> execute -> fresh screenshot -> plan ... until the model says done.
    Returns (final message, is_error); never opens a notice itself."""
    history: list[str] = []
    last_state, repeats = None, 0
    memory = ""  # the model's own notes (Plan.memory), carried from step to step
    used = vision_client.USAGE
    used.update(requests=0, input=0, output=0, cost=0.0)
    for step in range(1, MAX_STEPS + 1):
        if used["cost"] >= MAX_COST:
            return f"Task aborted: cost limit of ${MAX_COST:.2f} reached after {step - 1} steps (JARVIS_MAX_COST).", True
        try:
            plan = vision_client.plan(prompt, shot, history, memory)
        except Exception as e:  # API failure, bad key, hallucinated JSON...
            log.exception("planning failed")
            return f"Planning failed:\n{e}", True
        log.info("Thought: %s", plan.thought_process)
        if plan.memory and plan.memory != memory:
            log.info("Memory: %s", plan.memory)
        memory = plan.memory or memory  # an empty reply must not wipe notes the model still needs
        log.info("Step %d: %s (%d actions, done=%s)", step, plan.summary, len(plan.actions), plan.done)
        log.info("Usage so far: %d requests, %d in + %d out tokens, $%.4f",
                 used["requests"], used["input"], used["output"], used["cost"])
        if not plan.actions:  # done=True: goal verified on screen; else the model is blocked
            return (f"Task completed:\n{plan.summary}" if plan.done else plan.summary or "Nothing to do."), not plan.done
        acts = "; ".join(gui.describe(a) for a in plan.actions)  # exact actions go to the model's short-term memory
        # stuck = the same actions while the model's own notes stand still. The same click with NEW notes is
        # progress: in a quiz the right answer sits on the same button several questions in a row.
        state = (acts, memory)
        repeats = repeats + 1 if state == last_state else 1
        last_state = state
        if repeats >= MAX_REPEATS:  # stuck: stop before running (and paying for) the same thing again
            log.warning("Aborted: same actions %d times in a row: %s", repeats, acts)
            return f"Task aborted: the same actions repeated {MAX_REPEATS} times.\nActions: {acts}", True
        forced = check_forced_approval(plan.actions)  # code-level gate: holds even if the model was talked out of its flag
        if forced:
            log.warning("Security gate: %s", forced)
            plan.approval_reason = f"[SECURITY GATE] {forced}" + (f" | {plan.approval_reason}" if plan.approval_reason else "")
        if forced or plan.requires_approval:  # routine steps run straight through
            if not gui.confirm(plan, step):
                return f"Task cancelled by you at step {step}.", False
        else:
            log.info("Auto-approved: %s", acts)
        try:
            executor.run(plan.actions, shot.scale)
        except pyautogui.FailSafeException:
            return "Task stopped: the mouse was moved to a screen corner.", False
        except Exception as e:  # don't abort the loop: the next screenshot shows the model what went wrong
            log.exception("execution failed")
            history.append(f"{plan.summary} [{acts}] (FAILED with error: {e})")
        else:
            history.append(f"{plan.summary} [{acts}]")
        time.sleep(0.5)  # let the UI settle before looking again
        shot = screen_capture.capture()
    return f"Task aborted: step limit of {MAX_STEPS} exceeded.\nLast action: {history[-1] if history else '-'}", True


def preflight() -> bool:
    """Friendly exit instead of a stack trace when the client was built without a server address."""
    if vision_client.is_configured():
        return True
    msg = ("Jarvis does not know the address of its server yet.\n\n"
           "Start it as:  Jarvis_Portable.exe --server https://your-server-address\n"
           "or set DEFAULT_SERVER in vision_client.py and rebuild.")
    print(msg)
    gui.notify(msg, error=True)
    return False


def main() -> None:
    handlers = [logging.FileHandler(config.BASE / "jarvis.log", encoding="utf-8")] if getattr(sys, "frozen", False) else None
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", handlers=handlers)
    if "--voice-check" in sys.argv:  # diagnostic: is the audio stack usable in this build? Result goes to the log.
        log.info("Voice check: %s", voice.selfcheck())
        return
    if not preflight():
        sys.exit(1)
    threading.Thread(target=vision_client.warm_up, daemon=True, name="warm-up").start()
    current = vision_client.current_model()  # what .env / the environment asks for
    choices = MODEL_CHOICES if current in MODEL_CHOICES else [current] + MODEL_CHOICES
    os.environ["JARVIS_MODEL"] = gui.select_model(choices, current)  # asked once; vision_client reads it per request
    log.info("Model for this session: %s", os.environ["JARVIS_MODEL"])
    keyboard.add_hotkey(HOTKEY, on_hotkey)
    keyboard.add_hotkey(VOICE_HOTKEY, on_hotkey, args=(True,))
    keyboard.add_hotkey(QUIT_HOTKEY, quit_now)
    if os.getenv("TICKTICK_TOKEN"):
        ticktick.start(submit_task)
    log.info("Jarvis is idle. Press %s to type a command, %s to speak one (quit: %s).", HOTKEY, VOICE_HOTKEY, QUIT_HOTKEY)
    if getattr(sys, "frozen", False):  # no console: tell the user it is running
        gui.notify(f"Jarvis is running.\n\nPress {HOTKEY.title()} to type a command.\n"
                   f"Press {VOICE_HOTKEY.title()} to speak one.\nPress {QUIT_HOTKEY.title()} to quit.")
    try:
        while not stop.is_set():
            try:
                job = jobs.get(timeout=0.5)  # timeout keeps Ctrl+C / the quit hotkey responsive
            except queue.Empty:
                continue
            try:
                if isinstance(job, str):  # TickTick task: never start on its own, the user says yes first
                    if gui.ask_yes(f"TickTick task is due:\n\n{job}\n\nLet Jarvis do it?"):
                        handle(screen_capture.capture(), job)
                else:
                    by_voice, shot = job
                    handle_voice(shot) if by_voice else handle(shot)
            finally:
                busy.clear()
    except KeyboardInterrupt:
        pass
    log.info("Bye")


if __name__ == "__main__":
    main()
