"""
review_app.py - check and correct which cells were selected.
============================================================
The automatic picker is good, not perfect. This is where you overrule it.

Every image is shown with every object the segmenter found:

    GREEN outline   selected - this cell is being measured
    RED outline     rejected - not measured
    BLUE outline    a cell you drew yourself
    thicker line    you changed this one by hand

Click any cell to flip it between kept and rejected. If the segmenter missed a
cell completely, draw it. Nothing is deleted and nothing is edited in place:
your decisions are stored separately in curation.json and applied on top of the
automatic result, so the automatic run stays reproducible, every manual change
is visible in the output (the 'decided_by' column), and re-running never
silently throws your work away.

Fiji equivalent: this replaces "draw an ROI, add to ROI manager, redo the
measurement, re-export" with one click per cell.

Controls
  Left-click a cell     keep <-> reject
  Right-click a cell    delete a cell you drew (leaves automatic ones alone)
  D                     start drawing a missed cell; click points,
                        double-click or Enter to close, Esc to cancel
  N / Space             next image           P / Backspace   previous image
  H                     hide/show the rejected outlines
  [ / ]                 dim / brighten the fluorescence overlay
  R                     re-run the analysis with your corrections applied
  Q                     save and quit

Run:  python -m cell_viability.review_app "<dataset folder>"
"""
import os
import sys
import json
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cell_viability import livecell as LC      # noqa: E402
from cell_viability import rank_top as RT      # noqa: E402
from cell_viability import session as SS       # noqa: E402
from cell_viability import progress as PROG    # noqa: E402

DISPLAY_MAX = 900


def apply_decision(overrides, ckey, lid, want_selected, auto_selected):
    """Record one keep/reject decision. Pure logic, kept out of the GUI so it
    can be tested directly - this is where a silent bug once made every
    correction a no-op.

    Invariant: an override is stored ONLY when it disagrees with the model.
    Agreeing decisions remove the override, so curation.json always means
    exactly what it says and toggling twice leaves no residue.
    """
    o = overrides.setdefault(ckey, {"exclude": [], "include": []})
    for lst in (o["exclude"], o["include"]):
        while lid in lst:
            lst.remove(lid)
    if want_selected != auto_selected:
        (o["include"] if want_selected else o["exclude"]).append(lid)
    if not o["exclude"] and not o["include"]:
        overrides.pop(ckey, None)
    return overrides


def _candidates(ws):
    """condition/file -> {label_id: auto_selected} from the last run."""
    import pandas as pd
    for name in ("all_candidates.csv",):
        p = os.path.join(ws, name)
        if os.path.isfile(p):
            df = pd.read_csv(p)
            col = "auto_selected" if "auto_selected" in df.columns else "selected"
            out = {}
            for (c, f), g in df.groupby(["condition", "file"]):
                out[RT.curation_key(c, f)] = dict(
                    zip(g["label_id"].astype(int), g[col].astype(int)))
            return out
    return {}


def run(dataset_path, condition=None, master=None, outdir=None):
    """Open the review window.

    `master` must be passed when called from the main GUI. Tkinter allows only
    one Tk() root per process and is not thread-safe: creating a second root
    (or driving one from a worker thread) fails with errors like
    'image "pyimage2" doesn't exist'. With a master we attach as a Toplevel on
    the existing event loop instead.
    """
    import tkinter as tk
    from tkinter import messagebox, ttk
    from PIL import Image, ImageTk
    from skimage.segmentation import find_boundaries

    embedded = master is not None

    root_path = os.path.abspath(dataset_path)
    ws = LC.workspace(root_path)
    all_items = LC.list_images(root_path)
    if not all_items:
        raise SystemExit(f"No images found under {root_path}")

    SS.remember_project(root_path)
    sess = SS.load_session(ws)
    # Where results belong. Passed in by the GUI; otherwise reuse wherever the
    # last analysis wrote, so regenerating updates the folder the user already
    # has open rather than creating a second copy somewhere else.
    out_dir = outdir or sess.get("last_outdir") or None
    reviewed = SS.get_reviewed(sess)
    conditions = list(dict.fromkeys(i["condition"] for i in all_items))
    active_cond = condition or sess.get("review_condition") or "All folders"
    if active_cond not in conditions:
        active_cond = "All folders" if active_cond != "All folders" else active_cond

    def filtered(cond):
        if cond == "All folders":
            return list(all_items)
        return [i for i in all_items if i["condition"] == cond]

    items = filtered(active_cond)

    auto = _candidates(ws)
    if not auto:
        raise SystemExit(
            "No analysis found to review.\n"
            "Run the analysis once first (GUI: 'Brightest Live Cells'), then "
            "come back here to correct it.")

    cur = RT.load_curation(ws)
    S = dict(pos=0, masks=None, bf=None, dye=None, px=None, scale=1.0,
             dye_alpha=0.5, photo=None, show_rejected=True,
             drawing=False, current=[], manual_masks={})

    win = tk.Toplevel(master) if embedded else tk.Tk()
    win.title("Review selected cells")
    win.configure(bg="#1e1e1e")

    head = tk.Frame(win, bg="#1e1e1e")
    head.pack(fill="x", padx=10, pady=(8, 2))
    lbl_title = tk.Label(head, font=("Segoe UI", 12, "bold"), fg="#eee", bg="#1e1e1e")
    lbl_title.pack(side="left")
    lbl_counts = tk.Label(head, font=("Segoe UI", 10), fg="#9dd", bg="#1e1e1e")
    lbl_counts.pack(side="right")

    body = tk.Frame(win, bg="#1e1e1e")
    body.pack(fill="both", expand=True, padx=10)
    canvas = tk.Canvas(body, bg="black", highlightthickness=0, cursor="hand2")
    canvas.pack(side="left")

    side = tk.Frame(body, bg="#2a2a2a", width=270)
    side.pack(side="right", fill="y", padx=(10, 0))
    side.pack_propagate(False)

    tk.Label(side, text="Folder", font=("Segoe UI", 9, "bold"),
             fg="#eee", bg="#2a2a2a").pack(anchor="w", padx=8, pady=(8, 0))
    v_cond = tk.StringVar(value=active_cond)
    cb_cond = ttk.Combobox(side, textvariable=v_cond, state="readonly",
                           values=["All folders"] + conditions, width=28)
    cb_cond.pack(fill="x", padx=8, pady=(2, 6))

    lbl_prog = tk.Label(side, font=("Segoe UI", 9, "bold"), fg="#8fd",
                        bg="#2a2a2a", anchor="w")
    lbl_prog.pack(fill="x", padx=8)
    tk.Label(side, text="Images   (* = already reviewed)",
             font=("Segoe UI", 9, "bold"), fg="#eee", bg="#2a2a2a",
             anchor="w").pack(fill="x", padx=8, pady=(6, 2))
    lb = tk.Listbox(side, bg="#111", fg="#ddd", selectbackground="#357",
                    highlightthickness=0, borderwidth=0, activestyle="none",
                    font=("Consolas", 8))
    lb.pack(fill="both", expand=True, padx=8, pady=(0, 6))

    tk.Label(side, justify="left", anchor="w", fg="#aaa", bg="#2a2a2a",
             font=("Segoe UI", 8),
             text=("Click a cell: keep <-> reject\n"
                   "D: draw a missed cell\n"
                   "Right-click / X: not a cell,\n"
                   "     take it out altogether\n"
                   "N / P: next / previous\n"
                   "J: jump to first unreviewed\n"
                   "U: mark this one unreviewed\n"
                   "H: hide rejected   [ ]: dye\n"
                   "R: apply edits + regenerate\n"
                   "Q: save and quit")).pack(fill="x", padx=8, pady=(0, 6))
    btn_draw = tk.Button(side, text="Draw a missed cell (D)", relief="flat",
                         bg="#2a6", fg="white", font=("Segoe UI", 9, "bold"))
    btn_draw.pack(fill="x", padx=8, pady=(0, 4))
    btn_jump = tk.Button(side, text="Jump to first unreviewed (J)",
                         relief="flat", bg="#444", fg="white")
    btn_jump.pack(fill="x", padx=8, pady=(0, 4))
    v_teach = tk.BooleanVar(value=True)
    S["teach_var"] = v_teach
    tk.Checkbutton(side, text="Learn from my edits", variable=v_teach,
                   fg="#ddd", bg="#2a2a2a", selectcolor="#2a2a2a",
                   activebackground="#2a2a2a", activeforeground="#fff",
                   font=("Segoe UI", 8)).pack(anchor="w", padx=8)
    btn_rerun = tk.Button(side, text="Apply edits + regenerate (R)", bg="#357",
                          fg="white", relief="flat",
                          font=("Segoe UI", 10, "bold"))
    btn_rerun.pack(fill="x", padx=8, pady=(2, 8))

    lbl_out = tk.Label(side, font=("Segoe UI", 8), fg="#9c9", bg="#2a2a2a",
                       anchor="w", justify="left", wraplength=250)
    lbl_out.pack(fill="x", padx=8, pady=(0, 4))
    lbl_pending = tk.Label(side, font=("Segoe UI", 9, "bold"), fg="#fc6",
                           bg="#2a2a2a", anchor="w", justify="left",
                           wraplength=250)
    lbl_pending.pack(fill="x", padx=8, pady=(0, 6))

    footer = tk.Label(win, font=("Segoe UI", 9), fg="#888", bg="#1e1e1e", anchor="w")
    footer.pack(fill="x", padx=10, pady=(0, 8))

    # ---------- session / progress ----------
    def pair_of(it):
        return (it["condition"], it["file"])

    def refresh_pending():
        """Say plainly whether the results on disk match the current edits."""
        try:
            pending, n = RT.edits_pending(ws)
        except Exception:
            pending, n = False, 0
        lbl_out.config(text="Results go to:\n"
                            + (out_dir or "(the dataset's viability_workspace)"))
        if pending:
            lbl_pending.config(
                text=f"{n} edit(s) NOT yet in the results.\nPress R to rebuild.")
        elif n:
            lbl_pending.config(text=f"{n} edit(s), all applied to the results.")
        else:
            lbl_pending.config(text="")

    def persist():
        RT.save_curation(ws, cur)
        SS.set_reviewed(sess, reviewed)
        sess["review_condition"] = v_cond.get()
        if items:
            sess["review_last"] = list(pair_of(items[S["pos"]]))
        SS.save_session(ws, sess)

    def refresh_list():
        lb.delete(0, "end")
        for i, it in enumerate(items):
            mark = "*" if pair_of(it) in reviewed else " "
            lb.insert("end",
                      f"{mark}{i+1:2d}. {it['condition'][:11]:11s} {it['file'][:15]}")
            if pair_of(it) in reviewed:
                lb.itemconfig(i, fg="#6a6")
        done = sum(1 for it in items if pair_of(it) in reviewed)
        lbl_prog.config(text=f"Reviewed {done} of {len(items)} in this view")
        refresh_pending()
        if 0 <= S["pos"] < len(items):
            lb.selection_clear(0, "end")
            lb.selection_set(S["pos"])
            lb.see(S["pos"])

    def mark_reviewed_current():
        if items:
            reviewed.add(pair_of(items[S["pos"]]))

    # ---------- state helpers ----------
    def key():
        it = items[S["pos"]]
        return RT.curation_key(it["condition"], it["file"])

    def ov():
        return cur["overrides"].setdefault(key(), {"exclude": [], "include": []})

    def is_selected(lid):
        o = ov()
        if lid in S["manual_masks"]:
            return lid not in o["exclude"]
        a = bool(auto.get(key(), {}).get(lid, 0))
        if lid in o["include"]:
            return True
        if lid in o["exclude"]:
            return False
        return a

    def junk():
        """Ids marked "not a cell" for the image on screen."""
        return cur.setdefault("discard", {}).setdefault(key(), [])

    def is_junk(lid):
        return lid in junk()

    def set_junk(lid, on):
        """Mark an object as not-a-cell, or put it back.

        Kept separate from reject. Rejecting says "a real cell I do not want
        measured"; this says "that is not a cell". They mean different things
        to the models downstream, so they are stored differently.
        """
        lst = junk()
        while lid in lst:
            lst.remove(lid)
        if on:
            lst.append(lid)
            # It is not a cell, so a keep/reject opinion about it is
            # meaningless - drop any override so curation.json stays honest.
            o = ov()
            for l2 in (o["exclude"], o["include"]):
                while lid in l2:
                    l2.remove(lid)
            if not o["exclude"] and not o["include"]:
                cur["overrides"].pop(key(), None)
        if not lst:
            cur["discard"].pop(key(), None)

    def edited(lid):
        o = ov()
        return (lid in o["exclude"] or lid in o["include"]
                or lid in S["manual_masks"] or is_junk(lid))

    # ---------- rendering ----------
    def compose():
        b = LC.stretch(S["bf"])
        rgb = np.dstack([b, b, b])
        if S["dye"] is not None:
            rgb[..., 0] = np.clip(rgb[..., 0] + S["dye_alpha"] * LC.stretch(S["dye"]), 0, 1)
        rgb = (rgb * 255).astype(np.uint8)

        m = S["masks"]
        sel_ids, rej_ids, man_ids, edit_ids, junk_ids = [], [], [], [], []
        for lid in np.unique(m):
            if lid == 0:
                continue
            lid = int(lid)
            if is_junk(lid):
                junk_ids.append(lid)
                continue          # not a cell: it is neither kept nor rejected
            if lid in S["manual_masks"]:
                (man_ids if is_selected(lid) else rej_ids).append(lid)
            elif is_selected(lid):
                sel_ids.append(lid)
            else:
                rej_ids.append(lid)
            if edited(lid):
                edit_ids.append(lid)

        def paint(ids, colour, thick=False):
            if not ids:
                return
            reg = np.isin(m, ids)
            bnd = find_boundaries(reg, mode="outer")
            if thick:
                from scipy.ndimage import binary_dilation
                bnd = binary_dilation(bnd, iterations=1)
            rgb[bnd] = colour

        if S["show_rejected"]:
            paint(rej_ids, [170, 60, 60])
        paint(sel_ids, [0, 230, 120])
        paint(man_ids, [80, 170, 255])
        paint([i for i in edit_ids if is_selected(i)], [255, 220, 0], thick=True)
        # Faint, and drawn last so nothing hides it. Taken out of the analysis
        # but still findable, because the way to undo it is to click it again.
        paint(junk_ids, [90, 90, 90])
        return rgb, len(sel_ids) + len(man_ids), len(rej_ids), len(junk_ids)

    def redraw():
        rgb, n_sel, n_rej, n_junk = compose()
        im = Image.fromarray(rgb)
        w, h = im.size
        S["scale"] = DISPLAY_MAX / max(w, h)
        im = im.resize((int(w * S["scale"]), int(h * S["scale"])), Image.BILINEAR)
        S["photo"] = ImageTk.PhotoImage(im, master=win)
        canvas.delete("all")
        canvas.config(width=im.size[0], height=im.size[1])
        canvas.create_image(0, 0, anchor="nw", image=S["photo"])
        for i in range(len(S["current"]) - 1):
            x1, y1 = [v * S["scale"] for v in S["current"][i]]
            x2, y2 = [v * S["scale"] for v in S["current"][i + 1]]
            canvas.create_line(x1, y1, x2, y2, fill="#ffeb3b", width=2)
        for x, y in S["current"]:
            canvas.create_oval(x * S["scale"] - 3, y * S["scale"] - 3,
                               x * S["scale"] + 3, y * S["scale"] + 3,
                               fill="#ffeb3b", outline="#ffeb3b")
        o = ov()
        n_edits = len(o["exclude"]) + len(o["include"]) + n_junk
        lbl_counts.config(
            text=f"selected {n_sel}   rejected {n_rej}   "
                 + (f"not a cell {n_junk}   " if n_junk else "")
                 + f"your edits: {n_edits}"
                 + ("   [DRAWING - click points, Enter to close]" if S["drawing"] else ""))

    def load(pos):
        if not items:
            return
        S["pos"] = max(0, min(pos, len(items) - 1))
        pos = S["pos"]
        it = items[pos]
        refresh_list()
        lbl_title.config(text=f"[{pos+1}/{len(items)}]  {it['condition']} / {it['file']}")
        try:
            bf, dye, px = LC.load_channels(it["path"])
            masks = LC.cached_masks(it["path"], it["condition"], it["file"], ws,
                                    verbose=False)
        except Exception as exc:
            footer.config(text=f"Cannot load {it['file']}: {exc}")
            return
        S["bf"], S["dye"], S["px"] = bf, dye, px
        S["current"] = []
        S["drawing"] = False

        masks = masks.copy()
        S["manual_masks"] = {}
        polys = cur["manual"].get(key(), [])
        if polys:
            nxt = int(masks.max()) + 1
            for poly in polys:
                mm = LC.polygon_mask([(float(x), float(y)) for x, y in poly],
                                     masks.shape)
                if mm.sum() < 6:
                    continue
                masks[mm] = nxt
                S["manual_masks"][nxt] = poly
                nxt += 1
        S["masks"] = masks
        footer.config(text=f"{it['path']}")
        redraw()

    # ---------- interaction ----------
    def set_state(lid, want_selected):
        """Record a decision for one cell.

        If the decision agrees with what the model already did, the override is
        REMOVED rather than stored. Otherwise clicking a cell twice would leave
        a redundant entry that changes nothing while curation.json looked full
        of corrections - the file must mean exactly what it says.
        """
        ov()          # make sure the entry exists before deciding
        auto_sel = bool(auto.get(key(), {}).get(lid, 0)) or lid in S["manual_masks"]
        apply_decision(cur["overrides"], key(), lid, want_selected, auto_sel)

    def on_click(evt):
        if S["masks"] is None:
            return
        x, y = int(evt.x / S["scale"]), int(evt.y / S["scale"])
        if S["drawing"]:
            S["current"].append((x, y))
            redraw()
            return
        if not (0 <= y < S["masks"].shape[0] and 0 <= x < S["masks"].shape[1]):
            return
        lid = int(S["masks"][y, x])
        if lid == 0:
            footer.config(text="That is background - click on a cell.")
            return
        # Tk delivers two Button-1 events before a Double-Button-1, so an
        # ordinary double-click used to toggle twice and land back where it
        # started. Ignore the repeat.
        t = evt.time
        if S.get("last_click") == lid and t - S.get("last_time", 0) < 400:
            return
        S["last_click"], S["last_time"] = lid, t

        if is_junk(lid):
            footer.config(text=f"object {lid} is marked NOT A CELL - "
                               f"right-click it to put it back")
            return
        set_state(lid, not is_selected(lid))
        save()
        state = "KEPT (measured)" if is_selected(lid) else "REJECTED"
        footer.config(text=f"cell {lid}: {state}"
                           f"   (model said "
                           f"{'keep' if auto.get(key(), {}).get(lid, 0) else 'reject'})")
        redraw()

    def take_out(lid):
        """Right-click means the same thing everywhere: get rid of this
        outline. On a cell you drew, that deletes the drawing. On one the
        computer found, it says the object is not a cell."""
        if lid in S["manual_masks"]:
            poly = S["manual_masks"][lid]
            lst = cur["manual"].get(key(), [])
            if poly in lst:
                lst.remove(poly)
            if not lst:
                cur["manual"].pop(key(), None)
            save()
            load(S["pos"])
            footer.config(text="drawing deleted")
            return
        on = not is_junk(lid)
        set_junk(lid, on)
        save()
        redraw()
        footer.config(
            text=(f"object {lid}: NOT A CELL - left out of the analysis "
                  f"(right-click again to put it back)") if on
            else f"object {lid}: back in, as a cell")

    def on_right(evt):
        if S["masks"] is None or S["drawing"]:
            return
        x, y = int(evt.x / S["scale"]), int(evt.y / S["scale"])
        if not (0 <= y < S["masks"].shape[0] and 0 <= x < S["masks"].shape[1]):
            return
        lid = int(S["masks"][y, x])
        if lid == 0:
            footer.config(text="That is background - right-click on an outline.")
            return
        take_out(lid)

    def take_out_hovered(_=None):
        """Same thing from the keyboard, for the cell under the pointer."""
        if S["masks"] is None or S["drawing"]:
            return
        x = canvas.winfo_pointerx() - canvas.winfo_rootx()
        y = canvas.winfo_pointery() - canvas.winfo_rooty()
        x, y = int(x / S["scale"]), int(y / S["scale"])
        if not (0 <= y < S["masks"].shape[0] and 0 <= x < S["masks"].shape[1]):
            footer.config(text="Point at an outline, then press X.")
            return
        lid = int(S["masks"][y, x])
        if lid == 0:
            footer.config(text="Point at an outline, then press X.")
            return
        take_out(lid)

    def start_draw(_=None):
        S["drawing"] = True
        S["current"] = []
        redraw()

    def close_poly(_=None):
        if S["drawing"] and len(S["current"]) >= 3:
            cur["manual"].setdefault(key(), []).append(
                [[float(x), float(y)] for x, y in S["current"]])
            S["drawing"] = False
            S["current"] = []
            load(S["pos"])
        elif S["drawing"]:
            S["drawing"] = False
            S["current"] = []
            redraw()

    def cancel(_=None):
        S["drawing"] = False
        S["current"] = []
        redraw()

    def save():
        persist()

    def nxt(_=None):
        mark_reviewed_current()      # you looked at it, so it counts as done
        save()
        if S["pos"] < len(items) - 1:
            load(S["pos"] + 1)
        else:
            refresh_list()

    def prv(_=None):
        mark_reviewed_current()
        save()
        if S["pos"] > 0:
            load(S["pos"] - 1)
        else:
            refresh_list()

    def jump_unreviewed(_=None):
        mark_reviewed_current()
        for i, it in enumerate(items):
            if pair_of(it) not in reviewed:
                save()
                load(i)
                return
        save()
        refresh_list()
        messagebox.showinfo("All done",
                            "Every image in this view has been reviewed.")

    def unmark(_=None):
        if items:
            reviewed.discard(pair_of(items[S["pos"]]))
            save()
            refresh_list()

    def change_condition(_=None):
        nonlocal items
        mark_reviewed_current()
        save()
        items = filtered(v_cond.get())
        if not items:
            messagebox.showinfo("Empty", "No images in that folder.")
            items = filtered("All folders")
            v_cond.set("All folders")
        S["pos"] = 0
        load(0)

    def toggle_rej(_=None):
        S["show_rejected"] = not S["show_rejected"]
        redraw()

    def dye_dim(_=None):
        S["dye_alpha"] = max(0.0, S["dye_alpha"] - 0.1)
        redraw()

    def dye_up(_=None):
        S["dye_alpha"] = min(1.5, S["dye_alpha"] + 0.1)
        redraw()

    def rerun(_=None, confirm=True):
        """Apply the edits and rebuild every output, in one go.

        Retraining first means the picker learns from the corrections, so the
        regenerated run is better everywhere - not only on the cells that were
        touched by hand. The corrections are then applied on top, so nothing
        fixed manually can be undone by the retrained model.
        """
        save()
        n = sum(len(v.get("exclude", [])) + len(v.get("include", []))
                for v in cur["overrides"].values())
        m = sum(len(v) for v in cur["manual"].values())
        j = sum(len(v) for v in cur.get("discard", {}).values())
        if n + m + j == 0 and confirm:
            if not messagebox.askyesno(
                    "No edits yet",
                    "You have not changed any cells.\n\n"
                    "Regenerate the results anyway?"):
                return
        teach = (bool(S.get("teach_var") and S["teach_var"].get())
                 and (n + m + j > 0))
        where = out_dir or "the dataset's viability_workspace"
        if confirm and not messagebox.askyesno(
                "Apply edits and regenerate",
                f"{n} corrected and {m} drawn cell(s)"
                + (f", {j} marked not a cell" if j else "") + ".\n\n"
                + ("The picker will be retrained on your edits first.\n\n"
                   if teach else "")
                + f"Everything will be rebuilt in:\n{where}\n\n"
                  "Segmentation is cached, so this is quick. Continue?"):
            return

        footer.config(text="Working ...")

        # Retraining and rebuilding take a minute or two. Doing that on the
        # thread that draws this window stopped it answering Windows, which
        # then offered "Python is not responding - close the program?" over
        # the top of it. Closing there loses the edits. The work runs on a
        # worker thread now, behind a progress window that keeps moving.
        def work():
            """Runs on the worker thread. No Tk from in here."""
            note = None
            if teach:
                from cell_viability import pick_model as PM
                try:
                    PM.build_training_set(root_path)
                    model = PM.train(root_path)
                    print(f"[rank] picker retrained "
                          f"(AUC {model['cv_auc']:.3f})")
                except PM.NotEnoughLabels as few:
                    # Too few examples to fit a better picker. Keep the
                    # shipped one and carry on - the edits still take effect.
                    note = str(few)
                    print("[rank] kept the existing picker")
            live = RT.rank_top(root_path, outdir=out_dir,
                               top_n=RT.TOP_N, metric=RT.METRIC)
            return note, (0 if live is None else len(live))

        def done(result):
            note, n_live = result
            if note:
                messagebox.showinfo("Not enough edits to learn from yet", note)
            messagebox.showinfo(
                "Done",
                f"Rebuilt with your edits.\n\n"
                f"{n_live} live cells measured.\n\n"
                f"Results: {out_dir or 'viability_workspace/top_cells'}")
            footer.config(text=f"Done - {n_live} live cells. "
                               f"Results in {out_dir or 'the workspace'}")
            refresh_pending()
            # the picker changed, so refresh what the window shows as automatic
            auto.clear()
            auto.update(_candidates(ws))
            load(S["pos"])

        # SystemExit is how the analysis says "stop, here is why" - no
        # candidates, no dye column, a locked file. It is NOT an Exception, so
        # "except Exception" let it straight through Tk and killed the whole
        # window with no message at all.
        def failed(exc):
            if isinstance(exc, SystemExit):
                messagebox.showerror("Could not regenerate", str(exc)
                                     or "The analysis stopped without a reason.")
                footer.config(text=f"Stopped: {exc}")
            else:
                messagebox.showerror("Could not regenerate", str(exc))
                footer.config(text=f"Failed: {exc}")

        PROG.run_with_progress(
            win, "Rebuilding your results", work, on_done=done, on_error=failed,
            first_line="Applying your edits ...",
            note="Segmentation is cached, so this is usually quick. "
                 "Leave this open - it closes itself when the results are "
                 "ready.")

    def quit_save(_=None):
        save()
        try:
            pending, n = RT.edits_pending(ws)
        except Exception:
            pending, n = False, 0
        if pending:
            ans = messagebox.askyesnocancel(
                "Your edits are not in the results yet",
                f"You changed {n} cell(s), but the results folder still shows "
                f"the previous run.\n\n"
                f"Rebuild now so the spreadsheets, montages and images match "
                f"your edits?\n\n"
                f"Yes  - rebuild now\n"
                f"No   - close and keep the edits for later\n"
                f"Cancel - go back to reviewing")
            if ans is None:
                return
            if ans:
                rerun(confirm=False)
                return          # the rebuild reports its own result
        win.destroy()

    btn_rerun.configure(command=rerun)
    btn_jump.configure(command=jump_unreviewed)
    btn_draw.configure(command=start_draw)
    cb_cond.bind("<<ComboboxSelected>>", change_condition)
    win.bind("j", jump_unreviewed)
    win.bind("u", unmark)
    canvas.bind("<Button-1>", on_click)
    canvas.bind("<Button-3>", on_right)
    canvas.bind("<Double-Button-1>", close_poly)
    win.bind("<Return>", close_poly)
    win.bind("<Escape>", cancel)
    win.bind("d", start_draw)
    win.bind("x", take_out_hovered)
    win.bind("n", nxt)
    win.bind("<space>", nxt)
    win.bind("p", prv)
    win.bind("<BackSpace>", prv)
    win.bind("h", toggle_rej)
    win.bind("[", dye_dim)
    win.bind("]", dye_up)
    win.bind("r", rerun)
    win.bind("q", quit_save)
    win.protocol("WM_DELETE_WINDOW", quit_save)
    lb.bind("<<ListboxSelect>>",
            lambda e: (mark_reviewed_current(), save(), load(lb.curselection()[0]))
            if lb.curselection() and lb.curselection()[0] != S["pos"] else None)

    # ---------- resume where we left off ----------
    start = 0
    last = sess.get("review_last")
    if last:
        for i, it in enumerate(items):
            if list(pair_of(it)) == list(last):
                start = i
                break
    load(start)
    done = sum(1 for it in items if pair_of(it) in reviewed)
    if done:
        footer.config(text=f"Resumed: {done} of {len(items)} images already "
                           f"reviewed. Press J to jump to the first unreviewed.")
    if embedded:
        win.transient(master)
        win.focus_force()
        return win          # the caller's event loop is already running
    win.mainloop()
    save()
    print(f"Corrections saved -> {RT.curation_path(ws)}")
    print(f"Progress saved    -> {SS.session_path(ws)}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print('Usage: python -m cell_viability.review_app "<dataset folder>"')
        sys.exit(1)
    run(args[0])
