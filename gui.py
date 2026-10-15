"""Tk dialogs: command input, Human-in-the-Loop approval, notices. Main thread only."""
import tkinter as tk
from tkinter import messagebox, ttk

FONT = ("Segoe UI", 11)
APPROVE_DELAY_MS = 1500
KEY_GUARD_MS = 800  # notices and the command box ignore Enter/Escape/OK this long after they appear


def _window(title: str) -> tk.Tk:
    root = tk.Tk()
    root.title(title)
    root.attributes("-topmost", True)
    return root


def _show(root: tk.Tk, focus: tk.Widget) -> None:
    root.eval("tk::PlaceWindow . center")
    root.update()
    root.focus_force()
    focus.focus_force()
    root.mainloop()


def ask() -> str | None:
    root = _window("Jarvis")
    ttk.Label(root, text="What should I do?", font=FONT).pack(padx=16, pady=(14, 4), anchor="w")
    entry = ttk.Entry(root, width=60, font=FONT)
    entry.pack(padx=16, pady=(0, 14))
    out, armed = [], []
    root.after(KEY_GUARD_MS, lambda: armed.append(1))  # typing works at once; Enter/Escape only after the guard

    def submit(_=None):
        if armed:
            out.append(entry.get().strip())
            root.destroy()

    entry.bind("<Return>", submit)
    root.bind("<Escape>", lambda _: armed and root.destroy())
    _show(root, entry)
    return out[0] or None if out else None


def describe(a) -> str:
    d = a.model_dump(exclude_defaults=True)
    d.pop("action")
    return f"{a.action}  " + "  ".join(f"{k}={v!r}" for k, v in d.items())


def confirm(plan, step: int = 1) -> bool:
    """Approve/Cancel. Enter is deliberately NOT bound to Approve; closing the window cancels.
    Approve is dead for APPROVE_DELAY_MS and focus sits on Cancel, so a key already on its way cannot approve."""
    root = _window(f"Jarvis - step {step}: approve actions?")
    if plan.approval_reason:
        tk.Label(root, text=f"ATTENTION: {plan.approval_reason}", font=(FONT[0], 11, "bold"), fg="#b00020",
                 wraplength=560, justify="left").pack(padx=16, pady=(14, 0), anchor="w")
    ttk.Label(root, text=plan.summary or "Planned actions:", font=FONT, wraplength=560,
              justify="left").pack(padx=16, pady=(14, 8), anchor="w")
    box = tk.Text(root, width=70, height=min(len(plan.actions), 15), font=("Consolas", 10))
    box.insert("1.0", "\n".join(f"{i}. {describe(a)}" for i, a in enumerate(plan.actions, 1)))
    box.config(state="disabled")
    box.pack(padx=16)
    ok = []
    row = ttk.Frame(root)
    row.pack(pady=14)
    approve = ttk.Button(row, text="Approve", state="disabled", command=lambda: (ok.append(1), root.destroy()))
    approve.pack(side="left", padx=8)
    cancel = ttk.Button(row, text="Cancel", command=root.destroy)
    cancel.pack(side="left", padx=8)
    root.after(APPROVE_DELAY_MS, lambda: approve.config(state="normal"))
    root.bind("<Escape>", lambda _: root.destroy())
    _show(root, cancel)
    return bool(ok)


def select_model(available_models: list[str], default_model: str) -> str:
    """Start-up model picker. Closing the window (X or Escape) keeps default_model."""
    root = _window("Jarvis - model")
    ttk.Label(root, text="Which model should Jarvis use this session?", font=FONT).pack(padx=16, pady=(14, 6), anchor="w")
    choice = tk.StringVar(root, value=default_model)
    box = ttk.Combobox(root, textvariable=choice, values=available_models, state="readonly", width=42, font=FONT)
    box.pack(padx=16)
    out = []

    def ok(_=None):
        out.append(choice.get())
        root.destroy()

    ttk.Button(root, text="OK", command=ok).pack(pady=14)
    root.after(KEY_GUARD_MS, lambda: root.bind("<Return>", ok))  # a stray Enter must not pick for the user
    root.bind("<Escape>", lambda _: root.destroy())
    _show(root, box)
    return out[0] if out else default_model


def ask_yes(msg: str) -> bool:
    root = _window("Jarvis")
    root.withdraw()
    ok = messagebox.askyesno("Jarvis", msg, parent=root, default="no")  # a stray Enter must not start a task
    root.destroy()
    return ok


def notify(msg: str, error: bool = False) -> None:
    """Own window, not a native message box: that one closes on any stray Enter/Space the moment it appears.
    OK and the Enter/Escape keys only come alive after KEY_GUARD_MS; the X button works at once."""
    root = _window("Jarvis - error" if error else "Jarvis")
    tk.Label(root, text=msg, font=FONT, fg="#b00020" if error else "black", wraplength=520,
             justify="left").pack(padx=20, pady=(18, 12), anchor="w")
    ok = ttk.Button(root, text="OK", state="disabled", command=root.destroy)
    ok.pack(pady=(0, 14))

    def arm():
        ok.config(state="normal")
        ok.focus_set()
        for key in ("<Return>", "<Escape>"):
            root.bind(key, lambda _: root.destroy())

    root.after(KEY_GUARD_MS, arm)
    _show(root, root)
