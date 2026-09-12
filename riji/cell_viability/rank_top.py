"""
rank_top.py - select live cells, measure whole-cell uptake, rank PER FOLDER.
============================================================================
The final stage. For every image in the dataset:

  1. Segment whole cells from brightfield (cached from livecell.py).
  2. Score each candidate with the learned picker (pick_model.py) and keep the
     ones the human would have circled.
  3. Measure INTEGRATED Cy3 over the WHOLE CELL above a local background ring.
     Not the brightest region of the image. Not the brightest region inside the
     cell. The entire cell footprint, one number per cell.
  4. Rank cells WITHIN EACH FOLDER and export the top N.

Ranking is per folder because each folder is a separate experiment - a global
ranking would just sort the conditions against each other and tell you nothing
about which cell is a good representative of its own condition.

Outputs (under the chosen results folder):
  Results.xlsx                one workbook: intensities, summary, statistics,
                              per-image values and methods
  uptake_figure.png / .pdf    the figure
  <folder>/                   one per experiment folder: top-N crops, montage,
                              full-frame overlays
  data/                       raw per-cell tables, kept out of the way

The figure shows the SAME N cells per folder that were exported, so a request
for 20 gives 20 points. Because that is a fixed, equal rule applied to every
folder it is a defined sampling scheme, but it does sample the bright tail -
the workbook also reports the full selected population for comparison.

Run:  python -m cell_viability.rank_top "<dataset>" [--top=20] [--metric=cy3_integrated]
"""
import os
import sys
import json
import itertools
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cell_viability import livecell as LC          # noqa: E402
from cell_viability import pick_model as PM        # noqa: E402
from cell_viability import outputs as OUT          # noqa: E402

WORKSPACE_DIRNAME = LC.WORKSPACE_DIRNAME
METRIC_CHOICES = ("cy3_integrated", "cy3_mean")
CURATION_NAME = "curation.json"

# Settings the GUI writes before invoking quantify_live_cells().
TOP_N = 20
METRIC = "cy3_integrated"


# ──────────────────────────────────────────────────────────────────────────
#  Scoring + measuring
# ──────────────────────────────────────────────────────────────────────────

_PKG = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MODEL = LC.package_file("default_pick_model.json")
DEFAULT_MODEL_CONFLUENT = LC.package_file(
    "default_pick_model_confluent.json")

# How the culture is arranged. "auto" measures it; the GUI can force either.
LAYOUT = "auto"

# What to study. "live" measures healthy cells (the usual question); "dead"
# measures cells a human would score as dead, using a separate classifier -
# NOT simply the cells the live-cell picker rejected, most of which are alive
# but touching a neighbour.
TARGET = "live"

# Ranking picks the brightest N from each folder. If the folders yielded
# different numbers of usable cells, "the top N" is a different slice of each
# distribution: a shallow slice of a large pool against a deep slice of a small
# one, so the folders are no longer comparable. With this on, every folder's
# pool is first reduced to the size of the smallest, so N reaches the same
# depth everywhere. Reproducible: the subsample uses a fixed seed.
EQUALISE_DEPTH = False


def measure_confluence(root, ws, max_images=6):
    """How tightly packed is this culture?

    Returns the median fraction of each cell's boundary that touches another
    cell, over a sample of images. Sparse cultures sit near 0; a confluent
    monolayer approaches 1. This decides which picker to use, because
    "is the cell isolated?" is a decisive criterion in one regime and a
    meaningless one in the other.
    """
    items = LC.list_images(root)
    if not items:
        return 0.0, 0
    step = max(1, len(items) // max_images)
    vals, used = [], 0
    for it in items[::step][:max_images]:
        try:
            bf, _dye, px = LC.load_channels(it["path"])
            masks = LC.cached_masks(it["path"], it["condition"], it["file"],
                                    ws, verbose=False)
            feats, _k = LC.cell_features(masks, bf, px)
        except Exception:
            continue
        if feats:
            vals.extend(f["touch_frac"] for f in feats)
            used += 1
    if not vals:
        return 0.0, used
    return float(np.median(vals)), used


def load_picker(ws, root=None, layout=None):
    """Pick the right model for this dataset.

    A picker trained on THIS dataset's own annotations always wins. Otherwise
    one of the two shipped models is chosen by how packed the culture is.
    """
    local = os.path.join(ws, PM.MODEL_JSON)
    if os.path.isfile(local):
        with open(local, encoding="utf-8") as fh:
            return json.load(fh), "this dataset's own annotations"

    layout = layout or LAYOUT
    note = ""
    if layout == "auto" and root:
        touch, n_img = measure_confluence(root, ws)
        confluent = touch >= PM.CONFLUENT_TOUCH_FRAC
        note = (f"built-in default; cells touch neighbours over "
                f"{touch*100:.0f}% of their boundary (sampled {n_img} images) "
                f"-> treating as {'CONFLUENT' if confluent else 'SPARSE'}")
    else:
        confluent = (layout == "confluent")
        note = f"built-in default; layout forced to {layout.upper()}"

    path = DEFAULT_MODEL_CONFLUENT if confluent else DEFAULT_MODEL
    if not os.path.isfile(path):
        path = DEFAULT_MODEL
        note += " (confluent model missing, using the standard one)"
    if not os.path.isfile(path):
        raise SystemExit(
            "No cell picker available. Draw some example cells first:\n"
            '  python -m cell_viability.annotate_app "<dataset>"')
    with open(path, encoding="utf-8") as fh:
        return json.load(fh), note


def curation_path(ws):
    return os.path.join(ws, CURATION_NAME)


def load_curation(ws):
    """Human corrections applied on top of the automatic selection.

    Kept as a separate, explicit layer rather than edited into the results:
    the automatic result stays reproducible, every manual change is visible
    and reversible, and re-running never silently discards your edits.

    {"overrides": {"<condition>||<file>": {"exclude": [ids], "include": [ids]}},
     "manual":    {"<condition>||<file>": [[[x, y], ...], ...]},
     "discard":   {"<condition>||<file>": [ids]}}

    "exclude" and "discard" are deliberately different judgements. Excluding
    says "this IS a cell, but do not measure it" - a real cell that is touching
    a neighbour, half out of frame or out of focus. Discarding says "this is
    not a cell at all" - debris, a smear, a segmentation artefact. Collapsing
    the two would count rubbish as a rejected cell, teach the live/dead model
    that debris is a dead cell, and inflate every "objects found" number.
    """
    p = curation_path(ws)
    if not os.path.isfile(p):
        return {"overrides": {}, "manual": {}, "discard": {}}
    try:
        with open(p, encoding="utf-8") as fh:
            d = json.load(fh)
        d.setdefault("overrides", {})
        d.setdefault("manual", {})
        d.setdefault("discard", {})
        return d
    except Exception:
        return {"overrides": {}, "manual": {}, "discard": {}}


def save_curation(ws, data):
    os.makedirs(ws, exist_ok=True)
    with open(curation_path(ws), "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)


def curation_key(condition, file_name):
    return f"{condition}||{file_name}"


def discarded_ids(cur, condition, file_name):
    """Label ids the human said are not cells at all."""
    return set(int(i) for i in (cur or {}).get("discard", {}).get(
        curation_key(condition, file_name), []))


def masks_with_manual(src_path, condition, file_name, ws, cur, verbose=False,
                      drop_discarded=True):
    """Cached segmentation PLUS any cells drawn by hand, MINUS anything the
    human said is not a cell.

    Hand-drawn cells exist only in curation.json, not in the mask cache. Every
    stage that needs to find a cell by its label id must build the same
    combined label image, in the same order, or hand-drawn cells silently
    vanish - they were being measured and ranked but rendered no crop.

    Discarded objects are erased here, in the one place every stage goes
    through, so debris cannot come back as a red outline, an "objects found"
    count or a candidate to top up a short folder. Pass drop_discarded=False
    when training, which needs to see them to learn what to ignore.
    """
    masks = LC.cached_masks(src_path, condition, file_name, ws, verbose=verbose)
    polys = (cur or {}).get("manual", {}).get(
        curation_key(condition, file_name), [])
    junk = discarded_ids(cur, condition, file_name) if drop_discarded else set()
    if not polys and not junk:
        return masks, set()
    masks = masks.copy()
    # Number the hand-drawn cells from the ORIGINAL highest label, before
    # anything is erased. Taking the top object out would otherwise lower the
    # maximum and renumber every drawn cell, so a saved decision about "cell
    # 12" would silently start pointing at a different cell.
    nxt = int(masks.max()) + 1
    if junk:
        masks[np.isin(masks, list(junk))] = 0
    manual_ids = set()
    for poly in polys:
        m = LC.polygon_mask([(float(x), float(y)) for x, y in poly], masks.shape)
        if m.sum() < 6:
            continue
        masks[m] = nxt
        manual_ids.add(nxt)
        nxt += 1
    return masks, manual_ids


def curation_signature(cur):
    """Fingerprint of the edits, so we can tell whether the results on disk
    were built from the edits currently saved."""
    import hashlib
    payload = json.dumps(cur or {"overrides": {}, "manual": {}}, sort_keys=True)
    return hashlib.md5(payload.encode()).hexdigest()[:12]


def edits_pending(ws):
    """True when curation.json has changed since the last analysis run.

    Editing cells only records a decision; the results are rebuilt when the
    analysis is re-run. Without this check it is possible to correct a dozen
    cells, close the window, and later find the output folder unchanged with
    nothing having warned you.
    """
    from cell_viability import session as SS
    cur = load_curation(ws)
    n = sum(len(v.get("exclude", [])) + len(v.get("include", []))
            for v in cur["overrides"].values())
    n += sum(len(v) for v in cur["manual"].values())
    n += sum(len(v) for v in cur.get("discard", {}).values())
    if n == 0:
        return False, 0
    applied = SS.load_session(ws).get("curation_applied")
    return (curation_signature(cur) != applied), n


def score_dataset(root, verbose=True):
    """Segment, featurise, score and measure every image. Returns a DataFrame
    of every candidate object with its pick score and whole-cell uptake."""
    import pandas as pd

    root = os.path.abspath(root)
    ws = LC.workspace(root)
    os.makedirs(ws, exist_ok=True)
    cur = load_curation(ws)
    n_over = sum(len(v.get("exclude", [])) + len(v.get("include", []))
                 for v in cur["overrides"].values())
    n_man = sum(len(v) for v in cur["manual"].values())
    n_junk = sum(len(v) for v in cur.get("discard", {}).values())
    if n_over or n_man or n_junk:
        print(f"[rank] applying your corrections: {n_over} kept/removed by hand, "
              f"{n_man} cell(s) drawn manually, "
              f"{n_junk} marked not a cell", flush=True)
    if TARGET == "dead":
        from cell_viability import viability as VB
        M, src_desc = VB.load_model(ws)
        print(f"[rank] studying DEAD cells; model: {src_desc}", flush=True)
        print(f"[rank]   of the cells it calls dead, "
              f"{M.get('cv_precision', 0)*100:.0f}% really were "
              f"(held-out); it finds {M.get('cv_recall', 0)*100:.0f}% of them",
              flush=True)
    else:
        M, src_desc = load_picker(ws, root=root)
        print(f"[rank] cell picker: {src_desc}", flush=True)
    print(f"[rank]   variant={M.get('variant', 'sparse')}  "
          f"grouped-CV AUC {M.get('cv_auc', float('nan')):.3f}  "
          f"{len(M.get('features', []))} features", flush=True)
    noun = "dead" if TARGET == "dead" else "live"
    model = PM.load_model(M["model"])
    feats = M["features"]
    thr = float(M["threshold"])

    items = LC.list_images(root)
    rows = []
    for i, it in enumerate(items, 1):
        try:
            bf, dye, px = LC.load_channels(it["path"])
            masks = LC.cached_masks(it["path"], it["condition"], it["file"],
                                    ws, verbose=False)
        except Exception as exc:
            print(f"  [{i}/{len(items)}] {it['file']}: SKIP ({exc})", flush=True)
            continue

        key = curation_key(it["condition"], it["file"])
        ov = cur["overrides"].get(key, {})
        excl = set(int(i) for i in ov.get("exclude", []))
        incl = set(int(i) for i in ov.get("include", []))

        # Cells drawn by hand become real objects in the label image, so they
        # get the same features and the same measurement as everything else.
        masks, manual_ids = masks_with_manual(
            it["path"], it["condition"], it["file"], ws, cur)

        fr, _keep = LC.cell_features(masks, bf, px)
        if not fr:
            continue

        X = np.nan_to_num(
            np.array([[f[c] for c in feats] for f in fr], dtype=float),
            nan=0.0, posinf=0.0, neginf=0.0)
        scores = model.predict_proba(X)

        n_sel = 0
        for f, s in zip(fr, scores):
            lid = int(f["_label_id"])
            auto = bool(s >= thr)
            sel = (auto or lid in incl or lid in manual_ids) and lid not in excl
            source = ("manual" if lid in manual_ids
                      else "kept by hand" if lid in incl and not auto
                      else "removed by hand" if lid in excl and auto
                      else "automatic")
            rec = dict(
                condition=it["condition"], file=it["file"], src_path=it["path"],
                label_id=lid, x=round(f["_x"], 1), y=round(f["_y"], 1),
                bbox=json.dumps(f["_bbox"]),
                pick_score=round(float(s), 4), selected=int(sel),
                auto_selected=int(auto), decided_by=source,
                **{c: f[c] for c in feats},
            )
            if sel:
                mk = (masks == f["_label_id"])
                rec.update(LC.measure_uptake(mk, dye, px))
                n_sel += 1
            else:
                rec.update(dict(cy3_integrated=np.nan, cy3_mean=np.nan,
                                cy3_raw_mean=np.nan, cy3_raw_integrated=np.nan,
                                cy3_background=np.nan,
                                n_pixels=0))
            rows.append(rec)

        if verbose:
            print(f"  [{i}/{len(items)}] {it['condition'][:24]:24s} {it['file'][:20]:20s} "
                  f"{len(fr):3d} candidates -> {n_sel:3d} selected", flush=True)

    if not rows:
        raise SystemExit("No candidates found.")
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(ws, "all_candidates.csv"), index=False)
    return df


# ──────────────────────────────────────────────────────────────────────────
#  Rendering
# ──────────────────────────────────────────────────────────────────────────

def _composite(bf, dye, dye_gain=0.55):
    b = LC.stretch(bf)
    rgb = np.dstack([b, b, b])
    if dye is not None:
        rgb[..., 0] = np.clip(rgb[..., 0] + dye_gain * LC.stretch(dye), 0, 1)
    return (rgb * 255).astype(np.uint8)


def _font(size=14):
    """A readable font on any platform. macOS has no font called 'arial.ttf',
    so the Windows names alone silently fall back to a tiny bitmap font and the
    labels burned into the crops become unreadable."""
    from PIL import ImageFont
    for name in ("arial.ttf", "segoeui.ttf",                       # Windows
                 "/System/Library/Fonts/Supplemental/Arial.ttf",   # macOS
                 "/System/Library/Fonts/Helvetica.ttc",
                 "/Library/Fonts/Arial.ttf",
                 "DejaVuSans.ttf",                                 # Linux
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _crop_cell(rgb, mask, bbox, pad=38):
    """Crop around one cell and trace its measured boundary."""
    from PIL import Image, ImageDraw
    from skimage.segmentation import find_boundaries
    H, W = mask.shape
    mnr, mnc, mxr, mxc = bbox
    r0, c0 = max(0, mnr - pad), max(0, mnc - pad)
    r1, c1 = min(H, mxr + pad), min(W, mxc + pad)
    sub = rgb[r0:r1, c0:c1].copy()
    b = find_boundaries(mask[r0:r1, c0:c1], mode="outer")
    sub[b] = [0, 255, 136]
    return Image.fromarray(sub)


def _label_image(im, text, fill=(255, 235, 0)):
    from PIL import ImageDraw
    d = ImageDraw.Draw(im)
    f = _font(14)
    try:
        tw = d.textlength(text, font=f)
    except Exception:
        tw = 8 * len(text)
    d.rectangle([1, 1, 9 + int(tw), 21], fill=(0, 0, 0))
    d.text((5, 3), text, fill=fill, font=f)
    return im


def _label_image_bottom(im, text, fill=(255, 140, 140)):
    """Caption along the bottom edge - used to mark a crop that did not pass
    the live-cell filter and is only present to fill the requested count."""
    from PIL import ImageDraw
    d = ImageDraw.Draw(im)
    f = _font(12)
    w, h = im.size
    try:
        tw = d.textlength(text, font=f)
    except Exception:
        tw = 7 * len(text)
    d.rectangle([1, h - 18, 8 + int(tw), h - 1], fill=(0, 0, 0))
    d.text((4, h - 17), text, fill=fill, font=f)
    return im


def _montage(images, titles, out_path, cols=5, thumb=190):
    from PIL import Image, ImageDraw
    if not images:
        return None
    rows = (len(images) + cols - 1) // cols
    pad, hdr = 8, 20
    cell_w, cell_h = thumb, thumb + hdr
    sheet = Image.new("RGB", (cols * (cell_w + pad) + pad,
                              rows * (cell_h + pad) + pad), (18, 18, 18))
    d = ImageDraw.Draw(sheet)
    f = _font(13)
    for i, (im, t) in enumerate(zip(images, titles)):
        r, c = divmod(i, cols)
        x = pad + c * (cell_w + pad)
        y = pad + r * (cell_h + pad)
        im2 = im.copy()
        im2.thumbnail((cell_w, thumb))
        ox = x + (cell_w - im2.size[0]) // 2
        sheet.paste(im2, (ox, y))
        d.text((x + 2, y + thumb + 3), t, fill=(230, 230, 230), font=f)
    sheet.save(out_path)
    return out_path


def _overlay_frame(rgb, masks, sel_ids, rank_of, out_path, title):
    """Full frame: selected cells outlined green + numbered, rejected faint red."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from skimage.segmentation import find_boundaries

    img = rgb.copy()
    rejected = np.isin(masks, list(sel_ids), invert=True) & (masks > 0)
    if rejected.any():
        rb = find_boundaries(rejected, mode="outer")
        img[rb] = [150, 60, 60]
    if sel_ids:
        selm = np.isin(masks, list(sel_ids))
        sb = find_boundaries(selm, mode="outer")
        img[sb] = [0, 255, 136]

    fig, ax = plt.subplots(figsize=(9, 9))
    ax.imshow(img)
    for lid, (rk, cx, cy) in rank_of.items():
        ax.text(cx, cy, f"#{rk}", color="yellow", fontsize=9, weight="bold",
                ha="center", va="center")
    ax.set_title(title, fontsize=9)
    ax.axis("off")
    fig.savefig(out_path, dpi=95, bbox_inches="tight")
    plt.close(fig)


# ──────────────────────────────────────────────────────────────────────────
#  Figures + statistics (full live pool, NOT the top-N)
# ──────────────────────────────────────────────────────────────────────────

def _clear_outputs(cdir):
    """Remove previously generated crops/montage/CSV so a rerun cannot leave
    stale files from an older ranking mixed in with the current one."""
    import shutil
    for fn in os.listdir(cdir):
        p = os.path.join(cdir, fn)
        if os.path.isdir(p):
            if fn == "overlays":
                shutil.rmtree(p, ignore_errors=True)
        elif fn.startswith("rank") or fn in ("montage.png", "top_cells.csv"):
            try:
                os.remove(p)
            except OSError:
                pass


# ──────────────────────────────────────────────────────────────────────────
#  Main
# ──────────────────────────────────────────────────────────────────────────

def preflight(root):
    """Check the things that would otherwise fail AFTER a long segmentation.

    Ten minutes into a run is the worst possible time to discover that a folder
    has no fluorescence channel. Everything here is cheap - it reads one plane
    per folder - and it reports every problem at once rather than one per run.
    """
    problems, notes = [], []
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        return [f"Folder does not exist: {root}"], []

    items = LC.list_images(root)
    if not items:
        return ([f"No .czi/.tif/.nd2 images found under {root}.",
                 "Point this at the folder that CONTAINS your experiment "
                 "folders, not at one image."], [])

    by_cond = {}
    for it in items:
        by_cond.setdefault(it["condition"], []).append(it)
    notes.append(f"{len(items)} images in {len(by_cond)} folder(s)")

    n_dye = 0
    for cond, its in by_cond.items():
        probe = its[0]
        try:
            bf, dye, px = LC.load_channels(probe["path"])
        except Exception as exc:
            problems.append(f"'{cond}': cannot read {probe['file']} ({exc})")
            continue
        if dye is None:
            notes.append(f"'{cond}': no fluorescence channel - counted as a "
                         f"brightfield-only control, excluded from ranking")
        else:
            n_dye += 1
        if px <= 0:
            notes.append(f"'{cond}': no pixel size in the file metadata; "
                         f"areas will use the fallback scale")

    if n_dye == 0:
        problems.append("No folder has a fluorescence channel - there is "
                        "nothing to quantify.")
    try:
        import cellpose  # noqa: F401
    except Exception:
        problems.append("Cellpose is not installed, so cells cannot be "
                        "segmented (pip install cellpose).")
    return problems, notes


def locked_outputs(root_out):
    """Files in the results folder that are open in another program.

    Windows refuses to overwrite a file Excel has open. Discovering that after
    the analysis has run leaves the folder half-rewritten - some files updated,
    some not - which looks exactly like the edits not having been applied. So
    it is checked before any work starts.
    """
    if not os.path.isdir(root_out):
        return []
    stuck = []
    for dirpath, _dirs, files in os.walk(root_out):
        for fn in files:
            if not fn.lower().endswith((".csv", ".xlsx", ".png", ".pdf", ".txt")):
                continue
            p = os.path.join(dirpath, fn)
            try:
                with open(p, "ab"):
                    pass
            except PermissionError:
                stuck.append(os.path.relpath(p, root_out))
            except OSError:
                pass
    return stuck


def rank_top(root, outdir=None, top_n=20, metric="cy3_integrated",
             make_crops=True, make_overlays=True):
    """Select live cells, measure whole-cell uptake, export the top N PER FOLDER.

    `outdir` receives one sub-folder per experiment folder (plus _summary/).
    Defaults to <dataset>/viability_workspace/top_cells.
    """
    import pandas as pd

    if metric not in METRIC_CHOICES:
        raise SystemExit(f"--metric must be one of {METRIC_CHOICES}")

    root = os.path.abspath(root)
    problems, notes = preflight(root)
    for n in notes:
        print(f"[check] {n}", flush=True)
    if problems:
        for p in problems:
            print(f"[STOP] {p}", flush=True)
        # Say what is wrong IN the message. These reasons used to be printed
        # only to the log, so a window showing just the exception said "fix
        # the above" with nothing above it.
        raise SystemExit(
            "Nothing was processed:\n\n"
            + "\n".join(f"  - {p}" for p in problems))

    stuck = locked_outputs(os.path.abspath(outdir) if outdir else "")
    if stuck:
        print("[STOP] These result files are open in another program "
              "(usually Excel) and cannot be replaced:", flush=True)
        for f in stuck[:8]:
            print(f"         {f}", flush=True)
        if len(stuck) > 8:
            print(f"         ... and {len(stuck) - 8} more", flush=True)
        raise SystemExit(
            "These files are open in another program (usually Excel) and "
            "cannot be replaced:\n\n"
            + "\n".join(f"  {f}" for f in stuck[:8])
            + (f"\n  ... and {len(stuck) - 8} more" if len(stuck) > 8 else "")
            + "\n\nClose them and press the button again. Nothing was "
              "changed, so your previous results are still intact.")

    ws = LC.workspace(root)
    n_imgs = len(LC.list_images(root))
    n_cached = len([f for f in os.listdir(os.path.join(ws, LC.SEGCACHE_DIRNAME))
                    if f.endswith(".npz")]) if os.path.isdir(
                        os.path.join(ws, LC.SEGCACHE_DIRNAME)) else 0
    if n_cached < n_imgs:
        print(f"[rank] segmenting {n_imgs - n_cached} of {n_imgs} images "
              f"(~13 s each, first run only - cached afterwards)", flush=True)
    print(f"[rank] top {top_n} {'DEAD' if TARGET == 'dead' else 'live'} cells "
          f"PER FOLDER, ranked by {metric}", flush=True)
    df = score_dataset(root)

    live = df[df["selected"] == 1].copy()
    if live.empty:
        raise SystemExit("Picker selected no cells - threshold may be too strict.")

    live["uptake_rank"] = np.nan
    live["in_top_n"] = 0
    for cond, g in live.groupby("condition"):
        if not np.isfinite(g[metric]).any() or (g[metric].fillna(0) <= 0).all():
            continue                      # no-dye control: nothing to rank
        order = g[metric].rank(ascending=False, method="first")
        live.loc[g.index, "uptake_rank"] = order.values

    # ---------- output layout: one folder per experiment folder ----------
    # The chosen folder holds ONE folder per button, not the cell results
    # loose beside whatever colocalization writes.
    _chosen = os.path.abspath(outdir) if outdir else os.path.join(ws, "top_cells")
    root_out = os.path.join(_chosen, OUT.CELLS_ROOT_DIRNAME)
    os.makedirs(root_out, exist_ok=True)
    try:
        with open(os.path.join(_chosen, OUT.README), "w",
                  encoding="utf-8") as _fh:
            _fh.write(OUT.TOP_README_TEXT)
    except Exception:
        pass
    # The two things people open live at the top level; the audit trail sits
    # in data/ so the folder is not a wall of spreadsheets.
    # Grouped so the things that change together sit together.
    summary_dir = OUT.figures_dir(root_out, create=True)   # figure + workbook
    images_dir = OUT.images_dir(root_out, create=True)     # cell pictures
    data_dir = OUT.data_dir(root_out, create=True)         # raw tables
    with open(os.path.join(root_out, OUT.README), "w", encoding="utf-8") as _fh:
        _fh.write(OUT.README_TEXT)
    cur = load_curation(ws)          # needed to redraw hand-drawn cells

    # Results written by an earlier layout are moved aside, not deleted.
    OUT.retire_stale(root_out)

    simple_tables = {}

    # Remember where results go, so the review window can regenerate them in
    # the same place instead of quietly writing somewhere else.
    try:
        from cell_viability import session as SS
        _s = SS.load_session(ws)
        _s["last_outdir"] = root_out
        SS.save_session(ws, _s)
    except Exception:
        pass

    # A rerun REPLACES the results. Folders for conditions that no longer
    # exist would otherwise sit there looking current. Only directories this
    # tool wrote (they contain top_cells.csv) are removed.
    live_conds = {LC.safe_name(c) for c in df["condition"].unique()}
    try:
        _img = OUT.images_dir(root_out)
        for name in (os.listdir(_img) if os.path.isdir(_img) else []):
            d = os.path.join(_img, name)
            if (os.path.isdir(d) and name not in live_conds
                    and os.path.isfile(os.path.join(d, "top_cells.csv"))):
                import shutil
                shutil.rmtree(d, ignore_errors=True)
                print(f"[rank] removed stale folder: {name}", flush=True)
    except OSError:
        pass
    live.to_csv(os.path.join(data_dir, "live_cells.csv"), index=False)
    df.to_csv(os.path.join(data_dir, "all_candidates.csv"), index=False)

    print("\n[rank] selection summary (per folder = per experiment):")
    for cond, g in df.groupby("condition"):
        sel = int((g["selected"] == 1).sum())
        print(f"    {cond[:44]:44s} {len(g):4d} candidates -> {sel:4d} kept "
              f"({100*sel/max(1,len(g)):.0f}%)", flush=True)

    # How deep into each folder's population does the top N reach? Equal
    # counts from unequal pools are not comparable, so this is reported and can
    # be equalised.
    pools = {c: int((np.isfinite(g[metric]) & (g[metric] > 0)).sum())
             for c, g in live.groupby("condition")}
    pools = {c: n for c, n in pools.items() if n}
    smallest = min(pools.values()) if pools else 0
    if pools and len(pools) > 1:
        print("\n[rank] how deep the top-N reaches into each folder:")
        for c, n in pools.items():
            d = 100.0 * min(top_n, n) / n
            print(f"    {c[:44]:44s} {n:4d} cells -> top {d:5.1f}%", flush=True)
        depths = [100.0 * min(top_n, n) / n for n in pools.values()]
        if not EQUALISE_DEPTH and (max(depths) - min(depths)) > 10.0:
            print("    NOTE: these differ, so the folders are not being compared")
            print("          at the same depth. Turn on 'Compare folders at equal")
            print("          depth' to make the top-N mean the same everywhere.")
        if EQUALISE_DEPTH:
            print(f"    equalising: every folder reduced to {smallest} cells "
                  f"before ranking", flush=True)
            if smallest < top_n:
                limiting = [c for c, n in pools.items() if n == smallest]
                print(f"    NOTE: the smallest folder has only {smallest} cells, "
                      f"so no folder can reach {top_n}.")
                print(f"          limited by: {', '.join(limiting)[:60]}")
                print(f"          add cells there in Review Cells, or lower N.")

    shortfalls = []          # folders that could not supply N cells
    final_counts = {}        # what each folder actually exported
    for cond, g in live.groupby("condition"):
        gg = g[np.isfinite(g[metric]) & (g[metric] > 0)]
        if gg.empty:
            print(f"    [{cond[:34]}] no dye signal - skipping top-N")
            continue
        if EQUALISE_DEPTH and smallest and len(gg) > smallest:
            # fixed seed so the same cells are chosen on every run
            gg = gg.sample(n=smallest, random_state=0)
        gg = gg.assign(passed_filter=1)

        # Asked for N, deliver N. If fewer cells clear the picker, fall back to
        # the next-best candidates in that folder rather than returning a short
        # set. They are flagged passed_filter=0, shown as "below threshold" on
        # the crop, and DELIBERATELY EXCLUDED FROM THE STATISTICS, which always
        # use the cells that genuinely passed - otherwise topping up the export
        # would quietly change the result.
        if len(gg) < top_n:
            spare = df[(df["condition"] == cond) & (df["selected"] == 0)].copy()
            if len(spare):
                mk_by_file = {}
                extra = []
                for _, r in spare.sort_values("pick_score", ascending=False).iterrows():
                    key = (r["src_path"], r["file"])
                    if key not in mk_by_file:
                        try:
                            _bf, _dye, _px = LC.load_channels(r["src_path"])
                            _mk, _mi = masks_with_manual(
                                r["src_path"], cond, r["file"], ws, cur)
                            mk_by_file[key] = (_mk, _dye, _px)
                        except Exception:
                            continue
                    masks_f, dye_f, px_f = mk_by_file[key]
                    m = (masks_f == int(r["label_id"]))
                    if not m.any():
                        continue
                    vals = LC.measure_uptake(m, dye_f, px_f)
                    if vals["cy3_integrated"] <= 0:
                        continue
                    rr = r.to_dict()
                    rr.update(vals)
                    rr["passed_filter"] = 0
                    extra.append(rr)
                    if len(gg) + len(extra) >= top_n:
                        break
                if extra:
                    import pandas as _pd
                    gg = _pd.concat([gg, _pd.DataFrame(extra)], ignore_index=True)
                    print(f"    [{cond[:34]:34s}] only "
                          f"{int((gg['passed_filter']==1).sum())} cells passed the "
                          f"filter; added {len(extra)} next-best to reach {top_n} "
                          f"(marked below threshold, not used in statistics)")

        top = gg.sort_values(metric, ascending=False).head(top_n).copy()
        top.insert(0, "rank", range(1, len(top) + 1))
        final_counts[cond] = len(top)
        # A folder falls short when it cannot supply N cells THAT PASS THE
        # FILTER. Reaching N by topping up with below-threshold cells is not
        # the same thing: those are excluded from the statistics, so the folder
        # is still being compared on fewer real cells than the others.
        _passed = int((top.get("passed_filter", 1) == 1).sum())
        if _passed < top_n:
            shortfalls.append(dict(
                condition=cond, wanted=top_n, got=len(top),
                passed=_passed, filled=len(top) - _passed, pool=len(g)))
        live.loc[live.index.isin(top.index), "in_top_n"] = 1
        simple_tables[cond] = [
            (int(r["rank"]),
             f'{os.path.splitext(str(r["file"]))[0]}  x{int(r["x"])} y{int(r["y"])}',
             float(r[metric]), int(r.get("passed_filter", 1)))
            for _, r in top.iterrows()]

        cdir = os.path.join(images_dir, LC.safe_name(cond))
        os.makedirs(cdir, exist_ok=True)
        # This folder is regenerated from scratch every run. Without clearing,
        # crops from an earlier run (or from the manual annotate_app workflow,
        # which writes here too) survive alongside the new ones and you end up
        # with two different "rank01" files for the same condition.
        _clear_outputs(cdir)
        # The ranked listing and every per-cell measurement are sheets in
        # Results.xlsx ("Cells" and "Per cell"), so a per-condition CSV
        # here would be a third copy of the same numbers.

        if not (make_crops or make_overlays):
            continue

        thumbs, titles = [], []
        rank_by_file = {}
        for _, r in top.iterrows():
            rank_by_file.setdefault(r["file"], []).append(r)

        for fname, recs in rank_by_file.items():
            src = recs[0]["src_path"]
            try:
                bf, dye, px = LC.load_channels(src)
                masks, _manual = masks_with_manual(src, cond, fname, ws, cur)
            except Exception:
                continue
            rgb = _composite(bf, dye)

            if make_crops:
                for r in recs:
                    mk = (masks == int(r["label_id"]))
                    if not mk.any():
                        continue
                    im = _crop_cell(rgb, mk, json.loads(r["bbox"]))
                    txt = f"#{int(r['rank'])}  {r[metric]:,.0f}"
                    below = int(r.get("passed_filter", 1)) == 0
                    im = _label_image(im, txt,
                                      fill=(255, 140, 140) if below else (255, 235, 0))
                    if below:
                        im = _label_image_bottom(im, "below threshold")
                    stem = os.path.splitext(fname)[0]
                    im.save(os.path.join(
                        cdir,
                        f"rank{int(r['rank']):02d}_{LC.safe_name(stem)[:22]}"
                        f"_x{int(r['x'])}y{int(r['y'])}.png"))
                    thumbs.append(im)
                    titles.append(f"#{int(r['rank'])}  {fname[:12]}"
                                  + ("  (below thr)" if below else ""))

            if make_overlays:
                odir = os.path.join(cdir, "overlays")
                os.makedirs(odir, exist_ok=True)
                sel_here = live[(live["condition"] == cond) & (live["file"] == fname)]
                sel_ids = set(int(v) for v in sel_here["label_id"])
                rank_of = {int(r["label_id"]):
                           (int(r["rank"]), float(r["x"]), float(r["y"]))
                           for r in recs}
                _overlay_frame(
                    rgb, masks, sel_ids, rank_of,
                    os.path.join(odir, LC.safe_name(os.path.splitext(fname)[0]) + ".png"),
                    f"{cond} / {fname}   green=selected live, red=rejected, "
                    f"#=top-{top_n} by whole-cell uptake")

        if make_crops and thumbs:
            order = np.argsort([int(t.split()[0][1:]) for t in titles])
            _montage([thumbs[i] for i in order], [titles[i] for i in order],
                     os.path.join(cdir, "montage.png"))

        print(f"    [{cond[:34]:34s}] top {len(top)} -> {cdir}")


    # A folder that could not supply N cells has to say so. Otherwise "the top
    # 20 of each" quietly becomes "the top 20 of some and the top 14 of
    # another", which is no longer the same measurement in each folder.
    # The counts, and any shortfall, go into README.txt beside the results.
    try:
        with open(os.path.join(root_out, OUT.README), "w",
                  encoding="utf-8") as _fh:
            _fh.write(OUT.README_TEXT)
            _fh.write(OUT.run_notes(
                top_n, sorted(final_counts.items()), shortfalls,
                equalised=(smallest if EQUALISE_DEPTH else None)))
    except Exception:
        pass

    if shortfalls:
        print("")
        print("[rank] COULD NOT FIND " + str(top_n) + " CELLS IN:", flush=True)
        for sf in shortfalls:
            extra = (f", topped up with {sf['filled']} below threshold"
                     if sf["filled"] else "")
            print(f"    {sf['condition'][:40]:40s} only {sf['passed']:3d} of "
                  f"{sf['wanted']} cells passed{extra}", flush=True)
        print("    Those folders are being compared on fewer cells than the")
        print("    others. To fix it, open Review Cells, go to those folders and")
        print("    draw the cells it missed (press D), then press R to rebuild.")
        print("    Or lower N so every folder can reach it.")
        print("")

    # in_top_n is only known once every folder's top-N has been chosen, so the
    # table is rewritten here rather than before the loop.
    live.to_csv(os.path.join(data_dir, "live_cells.csv"), index=False)

    # Publication figure + Excel workbook (the deliverables for the manuscript)
    try:
        from cell_viability.report import build_report
        build_report(os.path.join(data_dir, "live_cells.csv"),
                     outdir=summary_dir, metric=metric, error="sd",
                     simple_tables=simple_tables)
    except Exception as exc:
        print(f"[rank] report step failed: {exc}")

    # Record that the results on disk now include the edits as they stand.
    try:
        from cell_viability import session as SS
        _s2 = SS.load_session(ws)
        _s2["curation_applied"] = curation_signature(cur)
        SS.save_session(ws, _s2)
    except Exception:
        pass

    print(f"\n[rank] RESULTS -> {root_out}")
    print(f"[rank]   one folder per experiment folder, {top_n} cells in each")
    print("[rank]   Figures and Tables/   figure + Results.xlsx")
    print("[rank]   Cell Images/          cell pictures + overlays")
    print("[rank]   data/                 raw tables")
    return live


def quantify_live_cells(folder, outdir=None):
    """Entry point used by the GUI button. Reads its settings from module
    globals so the GUI can set them without knowing the signature."""
    return rank_top(folder, outdir=outdir, top_n=TOP_N, metric=METRIC)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    top, metric, outdir = 20, "cy3_integrated", None
    for a in sys.argv[1:]:
        if a.startswith("--top="):
            top = int(a.split("=", 1)[1])
        if a.startswith("--metric="):
            metric = a.split("=", 1)[1]
        if a.startswith("--out="):
            outdir = a.split("=", 1)[1]
    if not args:
        print('Usage: python -m cell_viability.rank_top "<dataset>" '
              '[--top=20] [--metric=cy3_integrated|cy3_mean] [--out=FOLDER]')
        sys.exit(1)
    rank_top(args[0], outdir=outdir, top_n=top, metric=metric)
