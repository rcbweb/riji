"""
annotate_app.py - teach the tool which cells count as good cells.
==================================================================
You draw a polygon around each cell you would actually use: alive, spread,
isolated, whole. Those polygons are the ground truth the cell picker learns
from - they are the only place your judgement enters the pipeline.

You do NOT have to annotate every image, or every cell. A few dozen good
examples spread across conditions is enough; the picker generalises from them
to the whole dataset. Cells you skip are treated as weak negatives, so it
helps to work an image thoroughly once you start it.

The fluorescence channel is overlaid faintly (red) for context only. Never
choose a cell because it is bright - brightness is what gets measured
afterwards, and picking on it would make the result circular.

Outputs, under <dataset>/viability_workspace/:
  annotations/<condition>__<file>.json   your polygons per image (autosaved)

Pressing M trains the picker on these annotations and runs the full analysis
(pick_model.py -> rank_top.py). There is no separate measurement code here.

Run:  python -m cell_viability.annotate_app "<dataset folder>"

Controls:
  Left-click              add polygon vertex
  Double-click / Enter    close the polygon you are drawing
  Right-click on a poly   delete it
  Ctrl+Z                  undo last vertex (or last completed polygon)
  Escape                  cancel the polygon in progress
  N / Space               next image        P / Backspace   previous image
  [ / ]                   dim / brighten fluorescence overlay
  M                       train the picker and run the full analysis
  Q                       save + quit
"""
import os
import sys
import json
import numpy as np

WORKSPACE_DIRNAME = "viability_workspace"
DISPLAY_MAX = 950               # max on-screen dim (px) of the image canvas

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import coloc  # noqa: E402
from cell_viability import livecell as LC  # noqa: E402


# ---------- data helpers ----------

def _safe(s):
    return "".join(c if c.isalnum() or c in "-_ ." else "_" for c in str(s))


def _find_images(root):
    """{condition: [paths]} via the same rule build_catalog uses."""
    conds = coloc.find_conditions(root)
    items = []
    for cond, paths in conds.items():
        for p in paths:
            items.append(dict(condition=cond, file=os.path.basename(p), path=p))
    return items


def _load_channels(path):
    """Return (bf_2d, cy3_2d_or_None, px_um)."""
    arr = coloc.load_planes(path)
    chans = coloc.channel_info(path)
    bf_c = next((c for c in chans if c["transmitted"] and c["index"] < arr.shape[0]), None)
    dye_c = next((c for c in chans if not c["transmitted"] and c["index"] < arr.shape[0]), None)
    bf = arr[bf_c["index"]] if bf_c else arr[0]
    bf = bf.max(axis=0) if bf.ndim == 3 else bf
    dye = None
    if dye_c is not None:
        d = arr[dye_c["index"]]
        dye = (d.max(axis=0) if d.ndim == 3 else d).astype(np.float64)
    px = coloc.pixel_size_um(path) or coloc.FALLBACK_PIXEL_UM
    return bf.astype(np.float64), dye, float(px)


def _stretch(a, lo_p=1.0, hi_p=99.5):
    if a is None:
        return None
    lo, hi = np.percentile(a, lo_p), np.percentile(a, hi_p)
    return np.clip((a - lo) / max(1e-9, hi - lo), 0.0, 1.0)


def _polygon_mask(poly, shape):
    """Rasterize a polygon (list of (x,y) image coords) to a bool mask."""
    from PIL import Image as PImage, ImageDraw as PDraw
    im = PImage.new("L", (shape[1], shape[0]), 0)
    if len(poly) >= 3:
        PDraw.Draw(im).polygon([(float(x), float(y)) for x, y in poly], fill=1)
    return np.array(im, dtype=bool)


def _point_in_poly(x, y, poly):
    n = len(poly)
    if n < 3:
        return False
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > y) != (yj > y)) and \
           (x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi):
            inside = not inside
        j = i
    return inside


# ---------- annotation storage ----------

def _ann_path(ws, item):
    return os.path.join(ws, "annotations", _safe(f"{item['condition']}__{item['file']}") + ".json")


def _load_ann(path):
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return [list(map(lambda p: (float(p[0]), float(p[1])), pg)) for pg in data.get("polygons", [])]
    except Exception:
        return []


def _save_ann(path, item, img_shape, px_um, polys):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(dict(
            condition=item["condition"], file=item["file"],
            src_path=item["path"], img_shape=list(img_shape),
            px_um=px_um,
            polygons=[[[round(float(x), 2), round(float(y), 2)] for x, y in pg] for pg in polys],
        ), fh, indent=2)


# ---------- run the real pipeline on these annotations ----------

def measure_and_plot(dataset_path, top_n=20, metric="cy3_integrated"):
    """Train the cell picker from the polygons drawn here, then select,
    measure and rank per folder.

    This tool's job is to capture examples of good cells. The analysis itself
    lives in pick_model.py / rank_top.py so there is exactly one code path
    that produces results - no second, slightly-different implementation.
    """
    from cell_viability import pick_model as PM
    from cell_viability import rank_top as RT

    print("[run] training the cell picker from your annotations ...", flush=True)
    PM.build_training_set(dataset_path)
    PM.train(dataset_path)

    print("\n[run] selecting live cells and ranking per folder ...", flush=True)
    return RT.rank_top(dataset_path, top_n=top_n, metric=metric)



# ---------- GUI ----------

def run(dataset_path):
    import tkinter as tk
    from tkinter import ttk
    from PIL import Image, ImageTk

    root_path = os.path.abspath(dataset_path)
    items = _find_images(root_path)
    if not items:
        raise SystemExit(f"No images found under {root_path}")

    ws = LC.workspace(root_path)
    os.makedirs(os.path.join(ws, "annotations"), exist_ok=True)

    state = dict(
        pos=0,
        items=items,
        polys=[],            # list of list of (x,y) in IMAGE coords
        current=[],          # in-progress polygon vertices, IMAGE coords
        img_shape=None,
        px_um=None,
        scale=1.0,
        dye_alpha=0.45,
        base_photo=None,     # PhotoImage for current base image
        base_id=None,        # canvas item id of the image
        poly_ids=[],         # canvas item ids of finalized polygons
        current_line_ids=[], # canvas item ids of in-progress segments
        rubber_id=None,      # canvas rubber-band item
        bf=None, dye=None,
    )

    win = tk.Tk()
    win.title("Live-cell annotator")
    win.configure(bg="#1e1e1e")

    top = tk.Frame(win, bg="#1e1e1e")
    top.pack(fill="x", padx=10, pady=(8, 4))
    header = tk.Label(top, font=("Segoe UI", 12, "bold"), fg="#eee", bg="#1e1e1e")
    header.pack(side="left")
    counts_lbl = tk.Label(top, font=("Segoe UI", 10), fg="#9dd", bg="#1e1e1e")
    counts_lbl.pack(side="right")

    body = tk.Frame(win, bg="#1e1e1e")
    body.pack(fill="both", expand=True, padx=10)

    canvas = tk.Canvas(body, bg="black", cursor="crosshair", highlightthickness=0,
                       width=DISPLAY_MAX, height=DISPLAY_MAX)
    canvas.pack(side="left")

    side = tk.Frame(body, bg="#2a2a2a", width=260)
    side.pack(side="right", fill="y", padx=(10, 0))
    side.pack_propagate(False)

    tk.Label(side, text="Images", font=("Segoe UI", 11, "bold"),
             fg="#eee", bg="#2a2a2a").pack(anchor="w", padx=8, pady=(8, 2))
    listbox = tk.Listbox(side, bg="#111", fg="#ddd", selectbackground="#357",
                         highlightthickness=0, borderwidth=0, activestyle="none",
                         font=("Consolas", 9))
    listbox.pack(fill="both", expand=True, padx=8, pady=(0, 8))

    hint = tk.Label(side, font=("Segoe UI", 9), fg="#aaa", bg="#2a2a2a",
                    justify="left", anchor="w",
                    text=("Left-click: add point\n"
                          "Dbl-click / Enter: close polygon\n"
                          "Right-click: delete polygon\n"
                          "Ctrl+Z: undo    Esc: cancel\n"
                          "N / P: next / previous\n"
                          "[ / ]: dim/brighten dye\n"
                          "M: measure + plot   Q: save + quit"))
    hint.pack(fill="x", padx=8, pady=(0, 8))

    measure_btn = tk.Button(side, text="Measure and Plot (M)", bg="#357", fg="white",
                            relief="flat", font=("Segoe UI", 10, "bold"),
                            command=lambda: _do_measure())
    measure_btn.pack(fill="x", padx=8, pady=(0, 8))

    footer = tk.Label(win, font=("Segoe UI", 9), fg="#888", bg="#1e1e1e", anchor="w")
    footer.pack(fill="x", padx=10, pady=(0, 8))

    for i, it in enumerate(items):
        listbox.insert("end", f"{i+1:2d}. {it['condition'][:14]:14s}  {it['file']}")

    def _save_current():
        it = items[state["pos"]]
        _save_ann(_ann_path(ws, it), it, state["img_shape"], state["px_um"], state["polys"])

    def _totals():
        n = 0
        for jn in os.listdir(os.path.join(ws, "annotations")):
            if not jn.endswith(".json"):
                continue
            try:
                with open(os.path.join(ws, "annotations", jn), encoding="utf-8") as fh:
                    n += len(json.load(fh).get("polygons", []))
            except Exception:
                pass
        return n

    def _compose_base():
        bf = _stretch(state["bf"])
        dye = _stretch(state["dye"])
        if bf is None:
            return None
        rgb = np.dstack([bf, bf, bf])
        if dye is not None:
            rgb[..., 0] = np.clip(rgb[..., 0] + state["dye_alpha"] * dye, 0, 1)
        rgb = (rgb * 255).astype(np.uint8)
        im = Image.fromarray(rgb)
        w, h = im.size
        scale = DISPLAY_MAX / max(w, h)
        state["scale"] = scale
        im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.BILINEAR)
        return im

    def _load(pos):
        state["pos"] = pos
        listbox.selection_clear(0, "end")
        listbox.selection_set(pos)
        listbox.see(pos)
        it = items[pos]
        header.config(text=f"[{pos+1}/{len(items)}]  {it['condition']}  /  {it['file']}")
        try:
            bf, dye, px = _load_channels(it["path"])
        except Exception as exc:
            footer.config(text=f"Failed to load {it['file']}: {exc}")
            state["bf"] = state["dye"] = None
            canvas.delete("all")
            state["poly_ids"], state["current_line_ids"] = [], []
            state["polys"], state["current"] = [], []
            return
        state["bf"], state["dye"], state["px_um"] = bf, dye, px
        state["img_shape"] = bf.shape
        state["polys"] = _load_ann(_ann_path(ws, it))
        state["current"] = []

        canvas.delete("all")
        im = _compose_base()
        state["base_photo"] = ImageTk.PhotoImage(im)
        state["base_id"] = canvas.create_image(0, 0, anchor="nw", image=state["base_photo"])
        canvas.config(width=im.size[0], height=im.size[1])
        state["poly_ids"], state["current_line_ids"] = [], []
        state["rubber_id"] = None
        _redraw_polys()
        _refresh_counts()

    def _refresh_counts():
        counts_lbl.config(text=f"On this image: {len(state['polys'])}    "
                               f"Total drawn: {_totals()}")
        footer.config(text=f"Workspace: {ws}    Source: {items[state['pos']]['path']}")

    def _to_disp(x, y):
        s = state["scale"]
        return x * s, y * s

    def _redraw_polys():
        for pid in state["poly_ids"]:
            canvas.delete(pid)
        state["poly_ids"] = []
        for poly in state["polys"]:
            pts = []
            for x, y in poly:
                dx, dy = _to_disp(x, y)
                pts.extend([dx, dy])
            if len(pts) >= 6:
                pid = canvas.create_polygon(pts, outline="#00e676", fill="",
                                            width=2, activewidth=3)
                state["poly_ids"].append(pid)

    def _redraw_current():
        for lid in state["current_line_ids"]:
            canvas.delete(lid)
        state["current_line_ids"] = []
        if state["rubber_id"] is not None:
            canvas.delete(state["rubber_id"])
            state["rubber_id"] = None
        cur = state["current"]
        for i in range(len(cur) - 1):
            x1, y1 = _to_disp(*cur[i])
            x2, y2 = _to_disp(*cur[i + 1])
            lid = canvas.create_line(x1, y1, x2, y2, fill="#ffeb3b", width=2)
            state["current_line_ids"].append(lid)
        for x, y in cur:
            dx, dy = _to_disp(x, y)
            lid = canvas.create_oval(dx - 3, dy - 3, dx + 3, dy + 3,
                                     outline="#ffeb3b", fill="#ffeb3b")
            state["current_line_ids"].append(lid)

    def _on_left(evt):
        if state["bf"] is None:
            return
        s = state["scale"]
        x, y = evt.x / s, evt.y / s
        state["current"].append((x, y))
        _redraw_current()

    def _on_motion(evt):
        cur = state["current"]
        if not cur:
            return
        x1, y1 = _to_disp(*cur[-1])
        if state["rubber_id"] is None:
            state["rubber_id"] = canvas.create_line(x1, y1, evt.x, evt.y,
                                                    fill="#ffeb3b", width=1, dash=(3, 3))
        else:
            canvas.coords(state["rubber_id"], x1, y1, evt.x, evt.y)

    def _close_current(_=None):
        if len(state["current"]) >= 3:
            state["polys"].append(state["current"])
            state["current"] = []
            _redraw_polys()
            _redraw_current()
            _save_current()
            _refresh_counts()

    def _on_right(evt):
        if state["bf"] is None:
            return
        s = state["scale"]
        x, y = evt.x / s, evt.y / s
        for i in range(len(state["polys"]) - 1, -1, -1):
            if _point_in_poly(x, y, state["polys"][i]):
                del state["polys"][i]
                _redraw_polys()
                _save_current()
                _refresh_counts()
                return

    def _undo(_=None):
        if state["current"]:
            state["current"].pop()
            _redraw_current()
        elif state["polys"]:
            state["polys"].pop()
            _redraw_polys()
            _save_current()
            _refresh_counts()

    def _cancel(_=None):
        state["current"] = []
        _redraw_current()

    def _next(_=None):
        _save_current()
        if state["pos"] < len(items) - 1:
            _load(state["pos"] + 1)

    def _prev(_=None):
        _save_current()
        if state["pos"] > 0:
            _load(state["pos"] - 1)

    def _on_list_click(_):
        sel = listbox.curselection()
        if sel and sel[0] != state["pos"]:
            _save_current()
            _load(sel[0])

    def _dye_dim(_=None):
        state["dye_alpha"] = max(0.0, state["dye_alpha"] - 0.1)
        _redraw_base_only()

    def _dye_bright(_=None):
        state["dye_alpha"] = min(1.5, state["dye_alpha"] + 0.1)
        _redraw_base_only()

    def _redraw_base_only():
        im = _compose_base()
        if im is None:
            return
        state["base_photo"] = ImageTk.PhotoImage(im)
        canvas.itemconfig(state["base_id"], image=state["base_photo"])

    def _do_measure():
        _save_current()
        footer.config(text="Measuring... (this loads every image; give it a moment)")
        win.update_idletasks()
        try:
            measure_and_plot(root_path)
            footer.config(text=f"Done. Figure + CSVs written to {ws}")
        except SystemExit as exc:
            footer.config(text=f"Measure: {exc}")
        except Exception as exc:
            footer.config(text=f"Measure failed: {exc}")

    def _quit(_=None):
        _save_current()
        win.destroy()

    canvas.bind("<Button-1>", _on_left)
    canvas.bind("<Motion>", _on_motion)
    canvas.bind("<Double-Button-1>", _close_current)
    canvas.bind("<Button-3>", _on_right)
    win.bind("<Return>", _close_current)
    win.bind("<Control-z>", _undo)
    win.bind("<Escape>", _cancel)
    win.bind("<space>", _next)
    win.bind("n", _next)
    win.bind("<BackSpace>", _prev)
    win.bind("p", _prev)
    win.bind("[", _dye_dim)
    win.bind("]", _dye_bright)
    win.bind("m", lambda e: _do_measure())
    win.bind("q", _quit)
    win.protocol("WM_DELETE_WINDOW", _quit)
    listbox.bind("<<ListboxSelect>>", _on_list_click)

    _load(0)
    win.mainloop()


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print('Usage: python -m cell_viability.annotate_app "<dataset folder>"')
        print("       python -m cell_viability.annotate_app --measure \"<dataset folder>\"")
        sys.exit(1)
    if "--measure" in sys.argv:
        measure_and_plot(args[0])
    else:
        run(args[0])
