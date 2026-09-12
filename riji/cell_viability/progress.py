"""
progress.py - run a long job without freezing the window it was started from.
=============================================================================
Rebuilding the results takes a minute or two. It used to run on the very
thread that draws the window, so for that whole time the window could not
answer Windows, and Windows offered the scientist a dialog saying "Python is
not responding - close the program?". Closing it there throws away the edits
that have not been written yet, and the app looks like it crashed.

The work now runs on a worker thread behind a small progress window that
keeps moving and shows what the analysis is printing.

Tkinter may only be touched from the thread that created it, so nothing here
lets the worker draw anything: it sends text through a queue, and a poll on
the main thread moves it onto the screen. `on_done` and `on_error` are called
on the main thread too, so callers can update their window normally.
"""
import os
import sys
import queue
import threading
import tkinter as tk
from tkinter import ttk


class _Tee:
    """Send the worker's print() to the popup as well as the real console."""

    def __init__(self, q, real):
        self.q, self.real, self.buf = q, real, ""

    def write(self, s):
        try:
            self.real.write(s)
        except Exception:
            pass
        self.buf += s
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            line = line.rstrip()
            if line:
                self.q.put(("line", line))

    def flush(self):
        try:
            self.real.flush()
        except Exception:
            pass


def run_with_progress(parent, title, work, on_done=None, on_error=None,
                      first_line="Starting ...",
                      note="This can take a minute. The window will say when "
                           "it is done."):
    """Run `work()` off the main thread, behind a progress window.

    work()      called on a worker thread; whatever it returns is handed to
                on_done. Anything it raises - including SystemExit, which is
                how the analysis refuses - is handed to on_error instead.
    on_done     called on the MAIN thread with work()'s return value
    on_error    called on the MAIN thread with the exception

    Returns the popup, which closes itself when the job ends.
    """
    q = queue.Queue()

    top = tk.Toplevel(parent)
    top.title(title)
    top.resizable(False, False)
    try:
        top.transient(parent)
    except Exception:
        pass

    tk.Label(top, text=title, font=("Segoe UI", 11, "bold"),
             anchor="w").pack(fill="x", padx=16, pady=(14, 2))
    lbl = tk.Label(top, text=first_line, anchor="w", justify="left",
                   width=68, fg="#333")
    lbl.pack(fill="x", padx=16)
    bar = ttk.Progressbar(top, mode="indeterminate", length=460)
    bar.pack(padx=16, pady=8)
    bar.start(12)
    tk.Label(top, text=note, anchor="w", justify="left", fg="#777",
             wraplength=460).pack(fill="x", padx=16, pady=(0, 14))

    # Closing it mid-rebuild would leave the results folder half written, and
    # a worker thread cannot be stopped safely, so the X is ignored while the
    # job runs. It closes itself the moment the job ends.
    top.protocol("WM_DELETE_WINDOW", lambda: None)
    try:
        top.grab_set()
    except Exception:
        pass

    holder = {}

    def runner():
        real = sys.stdout
        sys.stdout = _Tee(q, real)
        try:
            holder["value"] = work()
            q.put(("done", None))
        except BaseException as exc:      # SystemExit is a refusal, not a bug
            q.put(("error", exc))
        finally:
            sys.stdout = real

    threading.Thread(target=runner, daemon=True).start()

    def finish(err):
        try:
            bar.stop()
            top.grab_release()
            top.destroy()
        except Exception:
            pass
        if err is not None:
            if on_error:
                on_error(err)
        elif on_done:
            on_done(holder.get("value"))

    def poll():
        try:
            while True:
                kind, payload = q.get_nowait()
                if kind == "line":
                    lbl.config(text=_short(payload))
                elif kind == "done":
                    finish(None)
                    return
                elif kind == "error":
                    finish(payload)
                    return
        except queue.Empty:
            pass
        try:
            top.after(80, poll)
        except Exception:
            pass

    top.after(80, poll)
    return top


def _short(line):
    """One readable line of progress, without the log's tag noise."""
    line = line.strip()
    for tag in ("[rank]", "[report]", "[train]", "[build]", "[check]",
                "[outputs]", "[viability]"):
        if line.startswith(tag):
            line = line[len(tag):].strip()
            break
    if len(line) > 88:
        line = line[:85] + "..."
    return line or "Working ..."
