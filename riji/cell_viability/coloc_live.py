"""
coloc_live.py - colocalization measured PER LIVE CELL, not per image.
=====================================================================
coloc.py computes colocalization over the whole frame. That mixes together
background, debris, dead cells and the cells you actually care about, and it
gives one number per image, so you cannot see cell-to-cell variability or run
statistics on cells.

This module reuses the vetted cell set: the same live, isolated, whole cells
selected (and hand-corrected) for the uptake analysis. For every such cell and
every pair of fluorescence channels it reports:

  pearson_r   correlation of the two channels INSIDE that cell. Computed over
              cell pixels only - a whole-image Pearson is dominated by shared
              background and is inflated almost by construction.
  M1          fraction of channel-A signal in the cell that sits on channel-B+
              pixels
  M2          fraction of channel-B signal in the cell that sits on channel-A+
              pixels
  overlap_area_frac, and the raw sums behind each ratio

Thresholds
----------
Costes thresholds are computed ONCE PER IMAGE and then applied inside each
cell. Deriving a threshold from a few hundred pixels of a single cell is
unstable and would make M1/M2 depend on cell size; a per-image threshold keeps
every cell in that image on the same footing. Falls back to Otsu if Costes
degenerates.

Reading the result
------------------
M1 and M2 are directional and it matters which is which. Every row names the
two roles explicitly (role_a, role_b), so "fraction of vesicle signal inside
lysosomes" is unambiguous rather than something you have to infer.

Run:  python -m cell_viability.coloc_live "<dataset>" [--out=FOLDER]
"""
import os
import sys
import json
import itertools
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import coloc                                    # noqa: E402
from cell_viability import livecell as LC       # noqa: E402
from cell_viability import rank_top as RT       # noqa: E402
from cell_viability import report as RP         # noqa: E402
from cell_viability import outputs as OUT       # noqa: E402
import coloc_outputs as CO                      # noqa: E402

MIN_CELL_PIXELS = 60          # below this, correlation inside a cell is noise


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
    except Exception:
        pass


def _write_workbook(df, summ, path, methods="", stats=""):
    """The one workbook for the in-cells run, through the shared writer.

    It had its own styling and its own column names, so it agreed with
    neither the whole-image workbook nor the cell one. Same writer now, so
    every column carries its unit, and a folder with a single cell says so
    instead of leaving the SD blank.
    """
    s2 = summ.copy()
    for col in ("pearson_sd", "M1_sd", "M2_sd"):
        if col in s2.columns and "n_cells" in s2.columns:
            s2[col] = [CO.NO_SD_CELLS if (n <= 1 or v != v)
                       else round(float(v), 4)
                       for v, n in zip(s2[col], s2["n_cells"])]

    CO.write_workbook(
        path,
        [("Summary", CO.tidy(s2)),
         ("Per cell", CO.tidy(df, drop=("src_path",)))],
        notes=[("How this was measured", methods or ""),
               ("Statistics", stats or ""),
               ("What these mean", CO.README_TEXT)])
    print(f"[coloc] workbook -> {path}")
    return path


def _methods_text(root, n_cells, n_images, pairs, conds):
    """The paragraph that justifies these numbers.

    Whole image/ has always written one. In cells/ - the folder whose numbers
    are the ones worth quoting - had none, so the measurement that most needs
    explaining was the one with nothing beside it saying how it was made.
    """
    import datetime
    return f"""HOW THIS WAS MEASURED  (colocalization inside cells)
{'=' * 68}
when      : {datetime.datetime.now():%Y-%m-%d %H:%M:%S}
images    : {root}
measured  : {n_cells} cells across {n_images} images
folders   : {', '.join(str(c) for c in conds)}
dye pairs : {', '.join(str(p) for p in pairs)}

WHICH CELLS
-----------
The same cells the uptake analysis measured: segmented from brightfield,
kept by the trained picker, plus every correction made by hand in the
review window - cells drawn in, cells dropped, objects marked "not a
cell". Nothing outside a kept cell contributes to any number here.

That is the difference from the whole-image folder. Background between
cells, debris and dead cells are excluded by construction rather than
hoped to average out.

THRESHOLDS
----------
Costes thresholds are computed ONCE PER IMAGE from the two channels of
that image, then applied inside every cell in it. Deriving a threshold
from the few hundred pixels of a single cell is unstable and would make
M1 and M2 depend on how big the cell is; one threshold per image keeps
every cell in that image on the same footing. Where the Costes fit
degenerates the fallback is Otsu, per image, the same way.

WHAT EACH NUMBER IS
-------------------
M1   of all the signal from dye A inside this cell, the fraction sitting
     on pixels where dye B is above its threshold.
M2   the same in reverse: the fraction of dye B sitting on dye A.
     M1 and M2 are DIFFERENT QUESTIONS and usually different numbers.
     Each row names role_a and role_b so which is which is never inferred.
r    Pearson correlation of the two channels across the pixels of that
     cell only. Runs -1 to +1. A whole-image Pearson is inflated by shared
     background; this one is not.

One row per cell per dye pair, so n is cells, not images.

CAVEAT WORTH KEEPING
--------------------
Cells from one image share a dish, a passage and a field of view, so they
are not fully independent. Where a difference rests on cells from very few
images, treat it as exploratory.

Z-stacks are flattened by maximum intensity projection before measuring,
because the cell outlines come from a single brightfield plane. On a thick
sample that can create overlap that is not real.
"""


def _stats_text(df, pairs):
    """Folder-against-folder comparisons, per cell, for each metric."""
    from scipy.stats import mannwhitneyu
    import itertools

    lines = ["STATISTICS  (colocalization inside cells)", "=" * 68,
             "Mann-Whitney U, two-sided, on the per-cell values.",
             "p is Bonferroni-corrected by the number of comparisons actually",
             "run for that metric and dye pair. n is CELLS. Cells from one",
             "image are not independent, so treat small-n results as",
             "exploratory rather than as proof.", ""]
    for pair in pairs:
        g = df[df["pair"] == pair]
        lines += [f"[{pair}]"]
        for metric, label in (("M1", "M1"), ("M2", "M2"),
                              ("pearson_r", "Pearson r")):
            conds = [c for c in dict.fromkeys(g["condition"])
                     if len(g.loc[g.condition == c, metric].dropna()) >= 3]
            combos = list(itertools.combinations(conds, 2))
            if not combos:
                continue
            lines.append(f"   {label}")
            for a, b in combos:
                va = g.loc[g.condition == a, metric].dropna()
                vb = g.loc[g.condition == b, metric].dropna()
                try:
                    _u, p = mannwhitneyu(va, vb, alternative="two-sided")
                except Exception:
                    continue
                pc = min(1.0, p * len(combos))
                star = ("***" if pc < 1e-3 else "**" if pc < 1e-2
                        else "*" if pc < 5e-2 else "ns")
                lines.append(f"      {str(a)[:28]:28s} (n={len(va):3d})  vs  "
                             f"{str(b)[:28]:28s} (n={len(vb):3d})   "
                             f"p={pc:.3g}  {star}")
            lines.append("")
        lines.append("")
    return "\n".join(lines)


def _middle_of(per_image):
    """The image whose cells sit in the middle of this folder.

    Each entry holds the M1 of every cell measured in that image. The image
    whose median is closest to the folder's median is the one that represents
    it; the first filename represents nothing but itself.
    """
    import numpy as _np
    if not per_image:
        return None
    scored = []
    for rec in per_image.values():
        vals = [v for v in rec.get("vals", []) if v == v]
        if not vals:
            continue
        rec = dict(rec)
        rec["m1"] = float(_np.median(vals))
        rec["n"] = len(vals)
        scored.append(rec)
    if not scored:
        first = list(per_image.values())[0]
        first = dict(first)
        first.setdefault("m1", float("nan"))
        first["note"] = ""
        return first
    folder_med = float(_np.median([r["m1"] for r in scored]))
    best = min(scored, key=lambda r: abs(r["m1"] - folder_med))
    best["note"] = ("(the only image)" if len(scored) == 1
                    else f"(middle of {len(scored)} images)")
    return best


def _panels(reps, outdir, verbose=True):
    """One panel per dye pair: the pictures behind the numbers.

    The whole-image button has always produced these, and they are how anyone
    checks a colocalization result is believable - whether the thresholds
    landed sensibly, whether the overlap is real or two dyes that merely fill
    the same cell. Measuring per cell produced a spreadsheet and nothing to
    look at, so the numbers had to be taken on trust.

    Same three rows as the whole-image panel, but everything outside the
    measured cells is dropped, because that is the whole point of this
    button: the scatter is built from cell pixels only, and the overlap is
    shown only where a measured cell is.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from skimage.segmentation import find_boundaries

    made = 0
    for pair, per_cond in sorted(reps.items()):
        conds = list(per_cond)
        if not conds:
            continue
        fig, axes = plt.subplots(3, len(conds), figsize=(4.5 * len(conds), 11),
                                 squeeze=False)
        drew = False
        for ci, cond in enumerate(conds):
            rep = _middle_of(per_cond[cond])
            if rep is None:
                continue
            try:
                imgs, colors, _meta = coloc.load_all(rep["path"])
                masks, _man = RT.masks_with_manual(
                    rep["path"], cond, rep["file"], rep["ws"],
                    RT.load_curation(rep["ws"]))
            except Exception:
                continue
            a, b = rep["roles"]
            if a not in imgs or b not in imgs:
                continue
            A, B = _flat(imgs[a]).astype(float), _flat(imgs[b]).astype(float)
            cells = np.isin(masks, list(rep["ids"]))
            if not cells.any():
                continue
            ta, tb = rep["ta"], rep["tb"]

            merged = np.clip(coloc._tint(coloc._stretch(A), colors.get(a) or "green")
                             + coloc._tint(coloc._stretch(B), colors.get(b) or "magenta"),
                             0, 1)

            # row 0 - what was measured, with the measured cells ringed
            ax = axes[0, ci]
            shown = merged.copy()
            shown[find_boundaries(cells, mode="outer")] = [1, 1, 0]
            ax.imshow(shown)
            ax.set_title(f"{cond}\n({rep['file']})  {len(rep['ids'])} cells  "
                         f"{rep.get('note', '')}", fontsize=8.5)
            ax.axis("off")

            # row 1 - the scatter, from CELL pixels only
            ax = axes[1, ci]
            av, bv = A[cells], B[cells]
            keep = np.isfinite(av) & np.isfinite(bv)
            ax.hexbin(np.clip(bv[keep], 1, None), np.clip(av[keep], 1, None),
                      gridsize=60, bins="log", xscale="log", yscale="log",
                      mincnt=1)
            ax.axvline(max(tb, 1), color="red", ls="--", lw=1)
            ax.axhline(max(ta, 1), color="red", ls="--", lw=1)
            r = rep.get("r")
            ax.set_title(f"in-cell R = {r:.2f} (red dashed = thresholds)"
                         if r is not None and np.isfinite(r)
                         else "in-cell pixels (red dashed = thresholds)",
                         fontsize=9)
            ax.set_xlabel(f"{b} intensity (log)", fontsize=8)
            ax.set_ylabel(f"{a} intensity (log)", fontsize=8)

            # row 2 - where the two actually overlap, inside those cells
            ax = axes[2, ci]
            both = (A > ta) & (B > tb) & cells
            out = merged.copy()
            out[~cells] *= 0.25            # dim everything not measured
            out[both] = [1, 1, 1]
            ax.imshow(out)
            ax.set_title(f"White = colocalized, inside cells "
                         f"(M1={rep['m1']:.2f})", fontsize=9)
            ax.axis("off")
            drew = True

        if drew:
            fig.suptitle(f"{pair}   -   measured inside cells", fontsize=12)
            fig.tight_layout(rect=(0, 0, 1, 0.97))
            p = os.path.join(
                CO.pair_dir(outdir, pair,
                            wanted=CO.wanted_set(coloc.PAIRS_WANTED)),
                CO.PANEL)
            fig.savefig(p, dpi=150)
            made += 1
        plt.close(fig)
    if verbose:
        print(f"[coloc] {made} panel(s) -> {outdir}")
    return made


# ──────────────────────────────────────────────────────────────────────────
#  Per-cell metrics
# ──────────────────────────────────────────────────────────────────────────

def _flat(a):
    return a.max(axis=0) if a.ndim == 3 else a


def _thresholds(A, B):
    """Costes for this image, Otsu as fallback. Returns (tA, tB).

    Costes only reports a threshold when its search actually converged; a
    status other than "ok" means it did not apply to this pair, and the old
    code took the floor value it returned as if it had.
    """
    try:
        ta, tb, info = coloc.costes_thresholds(A, B)
        if info.get("status") == "ok" and (A > ta).sum() and (B > tb).sum():
            return float(ta), float(tb)
    except Exception:
        pass
    from skimage.filters import threshold_otsu
    ta = float(threshold_otsu(A)) if A.max() > A.min() else float(A.max())
    tb = float(threshold_otsu(B)) if B.max() > B.min() else float(B.max())
    return ta, tb


def cell_coloc(A, B, cell, tA, tB):
    """Colocalization of two channels inside one cell mask."""
    from scipy.stats import pearsonr
    a, b = A[cell], B[cell]
    n = a.size
    if n < MIN_CELL_PIXELS:
        return None
    mA, mB = a > tA, b > tB
    both = mA & mB
    sa, sb = float(a[mA].sum()), float(b[mB].sum())
    try:
        r = float(pearsonr(a, b)[0])
    except Exception:
        r = float("nan")
    return dict(
        pearson_r=r,
        M1=float(a[both].sum() / sa) if sa > 0 else float("nan"),
        M2=float(b[both].sum() / sb) if sb > 0 else float("nan"),
        overlap_area_frac=float(both.sum() / n),
        area_frac_a_pos=float(mA.mean()),
        area_frac_b_pos=float(mB.mean()),
        sum_a=float(a.sum()), sum_b=float(b.sum()),
        n_pixels=int(n),
    )


# ──────────────────────────────────────────────────────────────────────────
#  Main
# ──────────────────────────────────────────────────────────────────────────

def _selection(root, ws):
    """{(condition, file): set(label_id)} of cells that passed the live filter,
    including any hand corrections."""
    import pandas as pd
    p = os.path.join(ws, "all_candidates.csv")
    if not os.path.isfile(p):
        raise SystemExit(
            "No cell selection found.\n"
            "This measures colocalization INSIDE the cells, so the cells\n"
            "have to be found first.\n\n"
            "Press '1. Find and measure cells', then this button again.\n\n"
            "(If you do not want cell-by-cell numbers, "
            "'Colocalization (whole image)'\n"
            "needs nothing beforehand.)")
    df = pd.read_csv(p)
    df = df[df["selected"] == 1]
    out = {}
    for (c, f), g in df.groupby(["condition", "file"]):
        out[(c, f)] = set(int(v) for v in g["label_id"])
    return out


def run(root, outdir=None, verbose=True):
    import pandas as pd

    root = os.path.abspath(root)
    ws = LC.workspace(root)
    sel = _selection(root, ws)
    # Colocalization/In cells/, beside the whole-image results. Given an
    # output folder this used to write its tables straight into the top of it,
    # loose among the cell results; given none it wrote inside the dataset's
    # own workspace, where nobody thinks to look for results at all.
    base_out = os.path.abspath(outdir) if outdir else OUT.results_root(ws)
    outdir = CO.root(base_out, "cells", create=True)
    datadir = CO.data_dir(outdir)
    _write(os.path.join(CO.root(base_out, create=True), CO.README),
           CO.README_TEXT)

    items = LC.list_images(root)

    # Say up front if a line in the Dyes box matches nothing in these images.
    # Otherwise the run reports whatever the microscope called the channel,
    # the folders come out named after that, and the mismatch is only
    # noticeable once everything has finished.
    if items:
        try:
            coloc.check_dye_names(items[0]["path"])
        except Exception:
            pass

    rows = []
    # first usable image of each folder, per dye pair, kept for the panels
    reps = {}
    for i, it in enumerate(items, 1):
        ids = sel.get((it["condition"], it["file"]))
        if not ids:
            continue
        try:
            imgs, _colors, meta = coloc.load_all(it["path"])
        except Exception as exc:
            print(f"  [{i}/{len(items)}] {it['file']}: SKIP ({exc})", flush=True)
            continue
        roles = list(imgs)
        if len(roles) < 2:
            if verbose:
                print(f"  [{i}/{len(items)}] {it['condition'][:22]:22s} "
                      f"{it['file'][:18]:18s} only {len(roles)} fluorescence "
                      f"channel - no pair to colocalize", flush=True)
            continue
        # Build the label image through the shared helper, not by repeating the
        # steps here. This code used to merge the hand-drawn cells itself, so
        # it numbered them from the un-edited mask; once an object could be
        # removed, the two numberings could drift apart and colocalization
        # would be reported against the wrong cell.
        try:
            masks, _manual = RT.masks_with_manual(
                it["path"], it["condition"], it["file"], ws,
                RT.load_curation(ws))
        except Exception:
            continue

        flats = {r: _flat(im).astype(np.float64) for r, im in imgs.items()}
        thr = {}
        _cw = CO.wanted_set(getattr(coloc, "PAIRS_WANTED", []))
        _all = getattr(coloc, "COMPUTE_ALL_PAIRS", True)
        pair_list = [(a, b) for a, b in itertools.combinations(roles, 2)
                     if _all or not _cw
                     or CO._roles_of(f"{a} vs {b}") in _cw]
        for a, b in pair_list:
            thr[(a, b)] = _thresholds(flats[a], flats[b])

        n_done = 0
        for lid in ids:
            cell = (masks == lid)
            if not cell.any():
                continue
            for a, b in pair_list:
                ta, tb = thr[(a, b)]
                m = cell_coloc(flats[a], flats[b], cell, ta, tb)
                if m is None:
                    continue
                pk = f"{a} vs {b}"
                cond_reps = reps.setdefault(pk, {})
                # Every image is kept as a candidate. Keeping only the first
                # made the picture that stands for a folder depend on how the
                # microscope numbered its files, so a reader could not tell
                # whether they were looking at a typical field or an unlucky
                # one. The middle one is chosen once the values are known.
                per_img = cond_reps.setdefault(it["condition"], {})
                if it["file"] not in per_img:
                    per_img[it["file"]] = dict(
                        path=it["path"], file=it["file"], ws=ws,
                        roles=(a, b), ta=ta, tb=tb, ids=set(ids), vals=[])
                per_img[it["file"]]["vals"].append(
                    m.get("M1", float("nan")))
                per_img[it["file"]]["r"] = m.get("pearson_r")
                rows.append(dict(
                    condition=it["condition"], file=it["file"],
                    label_id=int(lid), pair=f"{a} vs {b}",
                    role_a=a, role_b=b,
                    threshold_a=ta, threshold_b=tb, **m))
            n_done += 1
        if verbose:
            print(f"  [{i}/{len(items)}] {it['condition'][:22]:22s} "
                  f"{it['file'][:18]:18s} {n_done:3d} live cells x "
                  f"{len(list(itertools.combinations(roles, 2)))} pair(s)",
                  flush=True)

    if not rows:
        raise SystemExit(
            "No colocalization could be measured.\n"
            "This needs at least TWO fluorescence channels per image; this "
            "dataset appears to have one dye plus brightfield.")

    df = pd.DataFrame(rows)
    csv = os.path.join(datadir, CO.PER_CELL_CSV)
    df.to_csv(csv, index=False)
    print(f"\n[coloc] {len(df)} cell-pair measurements -> {csv}")

    # per condition x pair summary + a figure for each metric
    summ = (df.groupby(["pair", "condition"])
              .agg(n_cells=("pearson_r", "size"),
                   pearson_mean=("pearson_r", "mean"),
                   pearson_sd=("pearson_r", "std"),
                   M1_mean=("M1", "mean"), M1_sd=("M1", "std"),
                   M2_mean=("M2", "mean"), M2_sd=("M2", "std"))
              .reset_index())
    summ.to_csv(os.path.join(datadir, CO.SUMMARY_CSV), index=False)
    _pairs_seen = list(dict.fromkeys(df["pair"]))
    _conds_seen = list(dict.fromkeys(df["condition"]))
    _methods = _methods_text(root, int(df["label_id"].nunique()),
                             int(df["file"].nunique()), _pairs_seen,
                             _conds_seen)
    try:
        _stats = _stats_text(df, _pairs_seen)
    except Exception:
        _stats = ""
    _write_workbook(df, summ, os.path.join(outdir, CO.WORKBOOK),
                    methods=_methods, stats=_stats)

    made = []
    _want = CO.wanted_set(getattr(coloc, 'PAIRS_WANTED', []))
    # Every folder in a nested dataset carries the same parent, so the bars
    # were labelled "Fluo-protein / INF7 0.1", "Fluo-protein / INF7 0.5" ...
    # and the shared half, repeated five times and wrapped, ran the labels
    # into each other. It says nothing that distinguishes one bar from
    # another, so it is said once, in the axis label.
    shared, short = CO.shared_prefix(list(dict.fromkeys(df["condition"])))

    for pair, g in df.groupby("pair"):
        safe = LC.safe_name(pair)[:40]
        for metric, label in (("pearson_r", "Pearson r (within cell)"),
                              ("M1", "M1"), ("M2", "M2")):
            sub = g[np.isfinite(g[metric])]
            conds = list(dict.fromkeys(sub["condition"]))
            data = [sub.loc[sub.condition == c, metric].to_numpy(float)
                    for c in conds]
            data = [d for d in data if len(d) >= 2]
            conds = [c for c, d in zip(conds, [sub.loc[sub.condition == c, metric]
                                               .to_numpy(float) for c in conds])
                     if len(d) >= 2]
            if len(conds) < 1:
                continue
            ttl = f"{label}\n{pair}"
            # M1 and M2 are fractions and Pearson r cannot exceed 1 either, so
            # the ticks stop at 1. The axis itself still runs higher, because
            # that is where the significance brackets go - capping the limit
            # instead drew them outside the axes and through the title.
            _pd = CO.pair_dir(outdir, pair, wanted=_want)
            RP.make_figure(conds, data,
                           os.path.join(_pd, metric),
                           unit_note=label, ylabel=label, title=ttl,
                           metric=metric, error="sd",
                           labels=[short.get(c, c) for c in conds],
                           xlabel=shared, tick_max=1.0)
            made.append(metric)
        # this comparison's numbers, beside this comparison's charts
        try:
            _sub = summ[summ["pair"] == pair].copy()
            for _c in ("pearson_sd", "M1_sd", "M2_sd"):
                if _c in _sub.columns:
                    _sub[_c] = [CO.NO_SD_CELLS if (n <= 1 or v != v)
                                else round(float(v), 4)
                                for v, n in zip(_sub[_c], _sub["n_cells"])]
            CO.write_workbook(
                os.path.join(CO.pair_dir(outdir, pair, wanted=_want),
                             CO.PAIR_WORKBOOK),
                [("Per folder", CO.tidy(_sub, drop=("pair",))),
                 ("Per cell", CO.tidy(g, drop=("pair", "src_path")))],
                notes=[("What this is", str(pair) + chr(10) + chr(10)
                        + CO.README_TEXT)])
        except Exception:
            pass

    # the pictures behind the numbers, same three rows as the whole-image
    # panels but restricted to the cells that were actually measured
    try:
        _panels(reps, outdir, verbose=verbose)
    except Exception as exc:
        print(f"  [coloc] could not draw the panels: {exc}")

    _write(os.path.join(outdir, CO.RUNINFO), _methods)
    if _stats:
        _write(os.path.join(outdir, CO.STATS), _stats)

    print("\n[coloc] mean +/- SD per condition (each point is one live cell):")
    for _, r in summ.iterrows():
        print(f"    {r['pair'][:28]:28s} {r['condition'][:26]:26s} "
              f"n={int(r['n_cells']):3d}  r={r['pearson_mean']:+.3f}  "
              f"M1={r['M1_mean']:.3f}  M2={r['M2_mean']:.3f}")
    print(f"\n[coloc] figures + CSVs -> {outdir}")
    print("[coloc] M1/M2 are directional: role_a and role_b are named in "
          "every row of coloc_per_cell.csv")
    return csv


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    outdir = None
    for a in sys.argv[1:]:
        if a.startswith("--out="):
            outdir = a.split("=", 1)[1]
    if not args:
        print('Usage: python -m cell_viability.coloc_live "<dataset>" [--out=FOLDER]')
        sys.exit(1)
    run(args[0], outdir=outdir)
