"""
session.py - remember where you were, so work survives going home.
==================================================================
Two kinds of state, deliberately stored in two different places:

PROJECT STATE  ->  the dataset's working folder (livecell.workspace)
    Which images you have already reviewed, which one you were on, which
    condition you were filtering to. This belongs to the DATA, not to the
    computer: copy the dataset to another machine, or hand it to the person
    who takes over the project, and the progress goes with it. It also means
    two people working on different datasets never collide.

USER STATE     ->  ~/.riji/recent.json
    The folders you have opened recently, and the last output folder. This
    belongs to the PERSON: it is how the app opens on Monday already pointing
    at what you were doing on Friday, and it must not be written into the data
    (that would put your local paths into a shared dataset).

Everything is written immediately, not on exit, so a crash or a closed laptop
loses at most the last click.
"""
import os
import json
import datetime

SESSION_NAME = "session.json"
CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".riji")
RECENT_FILE = os.path.join(CONFIG_DIR, "recent.json")
MAX_RECENT = 10


def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


# ──────────────────────────────────────────────────────────────────────────
#  Project state (travels with the dataset)
# ──────────────────────────────────────────────────────────────────────────

def session_path(ws):
    return os.path.join(ws, SESSION_NAME)


def load_session(ws):
    p = session_path(ws)
    if not os.path.isfile(p):
        return {}
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def save_session(ws, data):
    try:
        os.makedirs(ws, exist_ok=True)
        data["updated"] = _now()
        with open(session_path(ws), "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
    except Exception:
        pass          # never let bookkeeping break the actual work


def get_reviewed(sess):
    """Set of (condition, file) already looked at."""
    return {tuple(x) for x in sess.get("reviewed", []) if len(x) == 2}


def set_reviewed(sess, pairs):
    sess["reviewed"] = sorted([list(p) for p in pairs])
    return sess


# ──────────────────────────────────────────────────────────────────────────
#  User state (recent projects)
# ──────────────────────────────────────────────────────────────────────────

def _load_recent():
    try:
        with open(RECENT_FILE, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save_recent(d):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(RECENT_FILE, "w", encoding="utf-8") as fh:
            json.dump(d, fh, indent=2)
    except Exception:
        pass


def remember_project(folder, outdir=None):
    """Record that this dataset was opened, most recent first."""
    if not folder:
        return
    folder = os.path.abspath(folder)
    d = _load_recent()
    items = [i for i in d.get("projects", [])
             if os.path.normcase(i.get("folder", "")) != os.path.normcase(folder)]
    items.insert(0, dict(folder=folder, outdir=outdir or "", opened=_now()))
    d["projects"] = items[:MAX_RECENT]
    _save_recent(d)


def recent_projects(existing_only=True):
    d = _load_recent()
    out = []
    for i in d.get("projects", []):
        f = i.get("folder", "")
        if existing_only and not os.path.isdir(f):
            continue
        out.append(i)
    return out


def project_progress(folder):
    """One-line 'where was I' for a dataset, for the recent-projects list."""
    from cell_viability import livecell as _lc
    ws = _lc.workspace(folder, create=False, verbose=False)
    sess = load_session(ws)
    rev = len(get_reviewed(sess))
    bits = []
    if rev:
        bits.append(f"{rev} image(s) reviewed")
    cur = os.path.join(ws, "curation.json")
    if os.path.isfile(cur):
        try:
            with open(cur, encoding="utf-8") as fh:
                c = json.load(fh)
            n = sum(len(v.get("exclude", [])) + len(v.get("include", []))
                    for v in c.get("overrides", {}).values())
            m = sum(len(v) for v in c.get("manual", {}).values())
            if n or m:
                bits.append(f"{n} corrected, {m} drawn")
        except Exception:
            pass
    if os.path.isfile(os.path.join(ws, "all_candidates.csv")):
        bits.append("analysed")
    return ", ".join(bits) if bits else "not started"
