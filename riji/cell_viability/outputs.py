"""
outputs.py - the one place that decides what results are called and where.
==========================================================================
Output names and folders used to be worked out ad hoc in each module. That is
how a renamed file ended up written under one name and read under another, and
how the figure editor came to save into data/ - because it derived its output
folder from wherever live_cells.csv happened to sit, and that file moved.

Import from here. Nothing else should contain an output filename or build a
results path by hand.

Layout of a results folder
--------------------------
    <results>/
        README.txt
        Figures and Tables/     the figure and the workbook - these change
                                together whenever the figure is edited, so
                                they live together
        Cell Images/            one folder per experiment folder: the ranked
                                cell pictures, the montage, and full-frame
                                overlays
        data/                   raw per-cell tables; nothing here needs opening
                                unless something looks wrong

"""
import os

# Everything the cell run makes lives under one folder, so the results
# directory has two things in it - what the cells run produced and what
# colocalization produced - instead of five siblings with no indication
# of which button made which.
CELLS_ROOT_DIRNAME = "Cell measurements"

FIGURES_DIRNAME = "Figures and Tables"
IMAGES_DIRNAME = "Cell Images"
DATA_DIRNAME = "data"
OVERLAY_DIRNAME = "overlays"


WORKBOOK = "Results.xlsx"
FIGURE_BASE = "uptake_figure"          # .png and .pdf
BOXPLOT = "uptake_box.png"
METHODS = "methods.txt"
SETTINGS = "figure_settings.json"
README = "README.txt"

LIVE_CELLS = "live_cells.csv"
ALL_CANDIDATES = "all_candidates.csv"
SUMMARY_CSV = "uptake_summary.csv"
STATS_CSV = "uptake_stats.csv"
TOP_CELLS_SUFFIX = "_top_cells.csv"
MONTAGE = "montage.png"

# Older layouts, still readable so existing results folders keep working.
LEGACY_SUMMARY_DIRNAME = "_summary"


def cells_root(results_dir):
    """The folder holding the CELL results, given the results folder.

    New runs put them in a bucket so colocalization can have one beside it.
    Older results have them loose at the top, and those folders still open, so
    the bucket is used when it is there and the folder itself when it is not.
    """
    d = os.path.join(results_dir, CELLS_ROOT_DIRNAME)
    return d if os.path.isdir(d) else results_dir


def figures_dir(results_dir, create=False):
    d = os.path.join(results_dir, FIGURES_DIRNAME)
    if create:
        os.makedirs(d, exist_ok=True)
    return d


def images_dir(results_dir, create=False):
    d = os.path.join(results_dir, IMAGES_DIRNAME)
    if create:
        os.makedirs(d, exist_ok=True)
    return d


def data_dir(results_dir, create=False):
    d = os.path.join(results_dir, DATA_DIRNAME)
    if create:
        os.makedirs(d, exist_ok=True)
    return d


def live_cells_path(results_dir):
    return os.path.join(data_dir(results_dir), LIVE_CELLS)


def find_live_cells(path):
    """Locate live_cells.csv for a results folder, newest layout first.

    Order matters: a leftover file from an older layout must never win, or a
    figure gets rebuilt from superseded numbers.
    """
    path = os.path.abspath(path)
    if os.path.isfile(path):
        return path
    for cand in (
        # pointed at the results folder itself, not the cell bucket inside it
        os.path.join(path, CELLS_ROOT_DIRNAME, DATA_DIRNAME, LIVE_CELLS),
        os.path.join(path, DATA_DIRNAME, LIVE_CELLS),
        os.path.join(path, LIVE_CELLS),
        os.path.join(path, LEGACY_SUMMARY_DIRNAME, LIVE_CELLS),
        os.path.join(path, "viability_workspace", "top_cells",
                     LEGACY_SUMMARY_DIRNAME, LIVE_CELLS),
    ):
        if os.path.isfile(cand):
            return cand
    raise SystemExit(
        f"No results found in {path}\n"
        "Point this at the folder the analysis wrote to.")


def results_root(path):
    """The results folder itself, given the folder OR a file inside it.

    The figure editor used to take the directory containing live_cells.csv as
    its output folder. When that table moved into data/, exports silently went
    to data/ while the dialog claimed they were in the results folder. Any code
    that needs "where do results belong" must ask this.
    """
    path = os.path.abspath(path)
    if os.path.isfile(path):
        path = os.path.dirname(path)
    # climb out of the sub-folders a results directory contains
    while os.path.basename(path) in (DATA_DIRNAME, FIGURES_DIRNAME,
                                     IMAGES_DIRNAME, LEGACY_SUMMARY_DIRNAME):
        parent = os.path.dirname(path)
        if not parent or parent == path:
            break
        path = parent
    return path


# ── what the figure shows ─────────────────────────────────────────────────
# "top" - the N cells per folder that were exported, so asking for 20 gives 20
# "all" - every cell that passed the live-cell filter
SCOPE_TOP = "top"
SCOPE_ALL = "all"
SCOPE_CHOICES = (SCOPE_TOP, SCOPE_ALL)
SCOPE_LABELS = {SCOPE_TOP: "Top N per folder (as exported)",
                SCOPE_ALL: "All selected cells"}


README_TEXT = """\
RESULTS
=======

Figures and Tables/
    uptake_figure.png / .pdf   the figure (.pdf is vector, for the paper)
    uptake_box.png             distribution view
    Results.xlsx               every number: Cells, For plotting, Summary,
                               Statistics, Per image, Per cell, Methods
    methods.txt                a methods paragraph with your numbers in it

    The figure and the workbook are rebuilt together, so they always agree.

Cell Images/
    one folder per experiment folder:
        rank01...rankN.png     the ranked cells, outlined, value on each
        montage.png            all of them on one sheet
        overlays/              full images - green = measured, red = rejected

data/
    live_cells.csv       every measured cell. The figure and the workbook are
                         rebuilt from this, so it has to stay.
    all_candidates.csv   every object found, and why it was kept or cut - the
                         only place that answers "why is that cell missing?"

    Nothing else is kept here. Anything else would be a second copy of a sheet
    in Results.xlsx, free to fall out of date.

Colocalization/
    Only here if you pressed one of the colocalization buttons. How much of
    one dye sits on top of another.
        In cells/       measured inside each measured cell - usually the one
                        you want
        Whole image/    measured over the whole frame, background included
    Each holds Colocalization.xlsx and one folder per comparison, named after
    it ("Vesicle in Lysosome"), holding that comparison's chart and pictures.
    Its own README.txt explains M and R. Nothing in there is produced by, or
    affects, the cell results above.

Two things worth knowing
------------------------
* The figure shows the top N cells per folder - the same cells exported as
  pictures. If the folders yielded different numbers of usable cells, "top N"
  is a different depth in each; the run prints those depths, and
  "Compare folders at equal depth" makes them the same.
* Statistics are reported per cell and per image. Cells in one image are not
  independent, so where the two disagree, quote the per-image value.
"""



TOP_README_TEXT = """\
RESULTS
=======

Two folders, one per button.

Cell measurements/
    Everything "1. Find and measure cells" produced: the uptake figure,
    Results.xlsx, the pictures of every measured cell, and the raw tables.
    Its own README.txt inside explains what each part is.

Colocalization/
    Only here if you pressed a colocalization button. How much of one dye
    sits on top of another, measured inside the cells and over the whole
    frame. Its own README.txt explains M and R and which folder to trust.

Nothing else is written at this level.
"""


def run_notes(top_n, per_folder, shortfalls, equalised=None):
    """The part of README.txt that describes THIS run."""
    lines = ["", "THIS RUN", "--------",
             "Requested %d cells per folder." % top_n]
    short = {s["condition"]: s for s in (shortfalls or [])}
    for cond, got in per_folder:
        sf = short.get(cond)
        if sf:
            mark = "   <-- only %d passed the filter" % sf["passed"]
            if sf.get("filled"):
                mark += ", %d below threshold" % sf["filled"]
        else:
            mark = ""
        lines.append("    %-40s %3d%s" % (str(cond)[:40], got, mark))
    if equalised:
        lines += ["",
                  "Folders were trimmed to %d cells before ranking," % equalised,
                  "so the top N reaches the same depth in each."]
    if shortfalls:
        lines += ["",
                  "SOME FOLDERS COULD NOT SUPPLY THE FULL NUMBER.",
                  "Those folders are compared on fewer cells than the others.",
                  "To fix it: open Review Cells, go to those folders, draw the",
                  "cells it missed (press D), then press R to rebuild.",
                  "Or lower N so every folder can reach it."]
    return "\n".join(lines) + "\n"


def retire_stale(root_out, verbose=True):
    """Move results left by an EARLIER LAYOUT into data/previous_run.

    An older build wrote the figure and workbook loose at the top level, and
    before that into an "_summary" folder. Leaving them means two Results.xlsx
    in one results folder, one of them stale, and a reader opening whichever
    they find first. Nothing is deleted - it is moved aside.

    This used to run only when the whole analysis re-ran, so a scientist who
    only edited the figure kept the duplicates forever. Every code path that
    writes results calls it now.
    """
    import shutil
    root_out = os.path.abspath(root_out)
    if not os.path.isdir(root_out):
        return []
    stale = [os.path.join(root_out, LEGACY_SUMMARY_DIRNAME)]
    stale += [os.path.join(root_out, f) for f in
              (WORKBOOK, FIGURE_BASE + ".png", FIGURE_BASE + ".pdf",
               BOXPLOT, METHODS, SETTINGS, LIVE_CELLS,
               ALL_CANDIDATES, SUMMARY_CSV, STATS_CSV)]
    # ...and the per-condition picture folders, which used to sit at the top
    # level before they were grouped under Cell Images/.
    for n in os.listdir(root_out):
        p = os.path.join(root_out, n)
        if (os.path.isdir(p)
                and n not in (FIGURES_DIRNAME, IMAGES_DIRNAME,
                              DATA_DIRNAME, LEGACY_SUMMARY_DIRNAME)
                and (os.path.isfile(os.path.join(p, MONTAGE))
                     or os.path.isdir(os.path.join(p, OVERLAY_DIRNAME)))):
            stale.append(p)

    stale = [p for p in stale if os.path.exists(p)]
    if not stale:
        return []
    moved = []
    try:
        bak = os.path.join(root_out, DATA_DIRNAME, "previous_run")
        os.makedirs(bak, exist_ok=True)
        for p in stale:
            dest = os.path.join(bak, os.path.basename(p))
            if os.path.exists(dest):
                (shutil.rmtree(dest, ignore_errors=True)
                 if os.path.isdir(dest) else os.remove(dest))
            shutil.move(p, dest)
            moved.append(os.path.basename(p))
    except Exception:
        pass
    if moved and verbose:
        print("[outputs] moved %d file(s) from an older layout into "
              "data/previous_run" % len(moved), flush=True)
    return moved
