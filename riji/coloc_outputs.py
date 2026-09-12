"""
coloc_outputs.py - what colocalization results are called, and where they go.
============================================================================
Separate from cell_viability/outputs.py on purpose. That module owns the
layout of the CELL results, which is settled and works; colocalization was
being added to it, which meant every change to one had the other's layout
sitting a few lines away. They are different questions with different
outputs, so they get different files. The only thing borrowed from the cell
side is "where is the results folder", which has to agree.

Layout
------
    <results>/
        Colocalization/
            README.txt              what M and R mean, which folder to trust

            In cells/               overlap inside each measured cell
                Colocalization.xlsx
                <A> vs <B>/         ONE FOLDER PER COMPARISON
                    example images.png
                    M1.png, M2.png, pearson_r.png
                data/

            Whole image/            overlap over the whole frame
                Colocalization.xlsx
                statistics.txt
                how this was measured.txt
                <A> in <B>/         ONE FOLDER PER COMPARISON
                    chart.png
                    example images.png
                Per folder/<condition>/matrix.png
                data/

A folder per comparison, because that is what a person is looking for. With
three dyes there are six of them, and the charts and the panels used to sit
in two separate folders keyed by long file names, so answering "how much
vesicle ended up in the lysosome" meant opening two folders and matching
titles. Now it is one folder with that name on it.
"""
import os
import re

DIRNAME = "Colocalization"
CELLS_DIRNAME = "In cells"          # coloc_live: inside each measured cell
WHOLE_DIRNAME = "Whole image"       # coloc: the whole frame
DATA_DIRNAME = "data"
# One Manders matrix per folder - a grid of every dye against every
# other. Useful occasionally, not what anyone came for, and named
# after what it is rather than after how it is split up.
PER_CONDITION_DIRNAME = "Manders matrix"
OTHER_DIRNAME = "Other permutations"

WORKBOOK = "Colocalization.xlsx"
STATS = "statistics.txt"
RUNINFO = "how this was measured.txt"
CODE = "coloc_USED.py"
PER_IMAGE_CSV = "coloc_per_image.csv"
SUMMARY_CSV = "coloc_summary.csv"
PER_CELL_CSV = "coloc_per_cell.csv"
CHANNELS_CSV = "channels_detected.csv"
README = "README.txt"

PANEL = "example images.png"
CONTACT_SHEET = "every image.png"
EACH_IMAGE_DIRNAME = "Each image on its own"
CHART = "chart.png"
PAIR_WORKBOOK = "numbers behind this chart.xlsx"

# Older layouts, still recognised so an existing run can be found.
LEGACY_DIRNAMES = ("coloc_results", "coloc_live")


def _safe(name):
    """A folder name a filesystem will accept, keeping it readable."""
    name = re.sub(r'[<>:"/\\|?*]', "-", str(name)).strip().strip(".")
    name = " ".join(name.split())
    return (name[:80] or "comparison")


def root(results_dir, which=None, create=False):
    """Where colocalization results belong.

    which=None       the Colocalization folder itself
    which="cells"    overlap measured inside each measured cell
    which="whole"    overlap over the whole frame
    """
    d = os.path.join(results_dir, DIRNAME)
    if which == "cells":
        d = os.path.join(d, CELLS_DIRNAME)
    elif which == "whole":
        d = os.path.join(d, WHOLE_DIRNAME)
    elif which is not None:
        raise ValueError(f"unknown colocalization kind: {which!r}")
    if create:
        os.makedirs(d, exist_ok=True)
    return d


def _roles_of(pair):
    """The two roles in a comparison string, however it was joined."""
    for sep in (" in ", " vs "):
        if sep in pair:
            a, b = pair.split(sep, 1)
            return frozenset((a.strip().lower(), b.strip().lower()))
    return frozenset((str(pair).strip().lower(),))


def wanted_set(rows):
    """The comparisons asked for in the window, as unordered role pairs.

    Both directions of one comparison count as the same request: someone who
    asks for "Vesicle in Lysosome" wants that comparison, and the reverse
    reading of it is the other half of the same answer.
    """
    out = set()
    for a, b in (rows or []):
        if a and b:
            out.add(frozenset((str(a).strip().lower(), str(b).strip().lower())))
    return out


def pair_dir(base, pair, create=True, wanted=None):
    """The folder for one comparison, named after it.

    `pair` is the human string - "Vesicle in Lysosome", "Cargo vs Vesicle".

    Three dyes make six comparisons and a scientist usually wants one or two
    of them. Sitting all six side by side left the answer being looked for
    indistinguishable from the four that came along with it, so anything not
    asked for in the window goes under "Other permutations" - kept, because
    it costs nothing and is occasionally the interesting one, but out of the
    way. With nothing requested, everything stays at the top as before.
    """
    if wanted and _roles_of(pair) not in wanted:
        base = os.path.join(base, OTHER_DIRNAME)
    d = os.path.join(base, _safe(pair))
    if create:
        os.makedirs(d, exist_ok=True)
    return d


def shared_prefix(conds):
    """Split folder names into the part they all share and the parts that differ.

    Nested datasets give every folder the same parent, so the bars were
    labelled "Fluo-protein / INF7 0.1", "Fluo-protein / INF7 0.5" and so on -
    the shared half repeated on every label, saying nothing that tells one bar
    from another, and wide enough to run them together.

    Returns (shared, {condition: short}). Nothing is shortened when there is
    one folder, or no common parent.
    """
    conds = [str(c) for c in conds]
    if len(conds) < 2:
        return "", {c: c for c in conds}
    parts = [c.split(" / ") for c in conds]
    n = 0
    while all(len(p) > n + 1 for p in parts) and len({p[n] for p in parts}) == 1:
        n += 1
    if n == 0:
        return "", {c: c for c in conds}
    return " / ".join(parts[0][:n]), {c: " / ".join(p[n:])
                                      for c, p in zip(conds, parts)}


def data_dir(base, create=True):
    d = os.path.join(base, DATA_DIRNAME)
    if create:
        os.makedirs(d, exist_ok=True)
    return d


README_TEXT = """\
====================================================================
  COLOCALIZATION
====================================================================
  How much of one dye sits on top of another.

  Two folders, because there are two ways to ask it:

  In cells\\        Measured INSIDE each cell that was measured for
                   uptake. One row per cell.
                   THIS IS USUALLY THE ONE YOU WANT. It ignores
                   debris, dead cells and empty background, none of
                   which belong in the answer.

  Whole image\\     Measured over the whole frame, cells and
                   background alike. The classic version, and what
                   to quote if you are comparing against a paper
                   that did it that way.

  The two will not agree, and neither is wrong. Whole-image numbers
  are pulled toward whatever fills most of the frame.


  WHAT IS IN EACH
--------------------------------------------------------------------
  Colocalization.xlsx      every number, in named sheets

  One folder per comparison, named after it, e.g.
      Vesicle in Lysosome\\
          chart.png            the comparison across your folders
          example images.png   the pictures behind the numbers:
                               the merged image, the intensity
                               scatter with the thresholds drawn on,
                               and the overlapping pixels in white

  data\\                    raw tables; ignore unless checking

  With three dyes there are six comparisons, so each gets its own
  folder rather than six charts in one pile and six panels in
  another.


  READING THE NUMBERS
--------------------------------------------------------------------
  M  (Manders)   the fraction of one dye's signal that sits on the
                 other. Runs 0 to 1. It is DIRECTIONAL: "A in B" and
                 "B in A" are different questions and usually have
                 different answers. Both are reported.

  R  (Pearson)   whether the two dyes brighten and dim together.
                 Runs -1 to +1. Sensitive to how much dye there is,
                 not just where it is.

  Quote M when the question is "how much of X reached Y".
--------------------------------------------------------------------
"""


# ── how every colocalization column is named, with its unit ───────────────
# Bare "M", "R", "mean", "std", "count" told a reader nothing: which of the
# two dyes M is about, what range R can take, whether count is cells or
# images. Every column says what it is and what it can be.
COLUMNS = {
    "condition":  "Folder",
    "file":       "Image",
    "pair":       "Comparison",
    "role_a":     "Dye A",
    "role_b":     "Dye B",
    "meaning":    "In words",
    "qc":         "Quality check",

    "M":          "M  (0-1)",
    "R":          "Pearson r  (-1 to +1)",
    "thresh_method": "Threshold method",
    "thresh_sig": "Cutoff, this dye  (intensity)",
    "thresh_ref": "Cutoff, other dye  (intensity)",
    "M_alt":      "M by the other rule  (0-1)",
    "M_alt_method": "The other rule",
    "M_spread":   "Gap between the two rules",
    "chance_M":   "M from chance alone  (0-1)",
    "enrichment": "Times above chance  (1 = chance)",
    "costes_p":   "Costes randomization  (%)",
    "mean":       "M, mean  (0-1)",
    "std":        "M, SD",
    "count":      "Images (n)",

    "M1":         "M1  (0-1)  fraction of A on B",
    "M2":         "M2  (0-1)  fraction of B on A",
    "pearson_r":  "Pearson r  (-1 to +1)",
    "label_id":   "Cell",
    "n_pixels":   "Cell area (pixels)",
    "overlap_area_frac": "Overlapping area  (0-1)",
    "n_cells":    "Cells (n)",
    "pearson_mean": "Pearson r, mean", "pearson_sd": "Pearson r, SD",
    "M1_mean":    "M1, mean  (0-1)", "M1_sd": "M1, SD",
    "M2_mean":    "M2, mean  (0-1)", "M2_sd": "M2, SD",
    "area_frac_a_pos": "Dye A above threshold  (0-1)",
    "area_frac_b_pos": "Dye B above threshold  (0-1)",
    "sum_a":      "Dye A total (AU)", "sum_b": "Dye B total (AU)",
}

# A single measurement has no spread. Saying so beats an empty cell, which
# reads as a number that went missing.
NO_SD = "- (single image)"
NO_SD_CELLS = "- (single cell)"


def tidy(df, drop=()):
    """Rename to the named columns, drop the ones that repeat themselves."""
    import pandas as pd   # noqa: F401
    d = df.drop(columns=[c for c in drop if c in df.columns], errors="ignore")
    return d.rename(columns={k: v for k, v in COLUMNS.items()
                             if k in d.columns})


def _number_format(header):
    h = str(header)
    if h.startswith("p ") or h.startswith("p("):
        return "0.000"
    if "(n)" in h or h in ("Cell", "#"):
        return "0"
    if "(pixels)" in h:
        return "#,##0"
    if "(AU)" in h or "(intensity)" in h:
        return "#,##0"
    if h.startswith("Costes randomization"):
        return "0.0"
    if h.startswith("Times above chance") or h.startswith("Gap between"):
        return "0.00"
    if "0-1" in h or "-1 to +1" in h or "mean" in h or "SD" in h:
        return "0.000"
    return None


def write_workbook(path, sheets, notes=None):
    """Write a styled workbook. `sheets` is [(name, DataFrame), ...].

    Both colocalization paths wrote their own, so one had units and the other
    did not and neither matched the cell workbook. There is one writer now.
    """
    import pandas as pd
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
    from openpyxl.utils import get_column_letter

    head_fill = PatternFill("solid", fgColor="2C3E50")
    band = PatternFill("solid", fgColor="F4F7FA")
    thin = Side(style="thin", color="D0D7DE")
    box = Border(left=thin, right=thin, top=thin, bottom=thin)

    try:
        with pd.ExcelWriter(path, engine="openpyxl") as xl:
            for name, df in sheets:
                if df is None or not len(df):
                    continue
                df.to_excel(xl, sheet_name=name[:31], index=False)
                sh = xl.sheets[name[:31]]
                head = [c.value for c in sh[1]]
                fmts = [_number_format(h) for h in head]
                for c in sh[1]:
                    if c.value is None:
                        continue
                    c.font = Font(bold=True, color="FFFFFF", size=10)
                    c.fill = head_fill
                    c.alignment = Alignment(horizontal="center",
                                            vertical="center", wrap_text=True)
                    c.border = box
                sh.row_dimensions[1].height = 34
                for i, row in enumerate(sh.iter_rows(min_row=2)):
                    for cell in row:
                        cell.border = box
                        if i % 2:
                            cell.fill = band
                        j = cell.column - 1
                        if (j < len(fmts) and fmts[j]
                                and isinstance(cell.value, (int, float))):
                            cell.number_format = fmts[j]
                for col in sh.columns:
                    w = max((len(str(c.value)) for c in col[:80]
                             if c.value is not None), default=10)
                    sh.column_dimensions[
                        get_column_letter(col[0].column)].width = min(
                            34, max(12, w + 3))
                sh.freeze_panes = sh.cell(row=2, column=1)
                if sh.max_row > 4:
                    sh.auto_filter.ref = (
                        f"A1:{get_column_letter(max(1, len(head)))}"
                        f"{sh.max_row}")

            for label, text in (notes or []):
                pd.DataFrame({"": str(text).split("\n")}).to_excel(
                    xl, sheet_name=label[:31], index=False, header=False)
                xl.sheets[label[:31]].column_dimensions["A"].width = 88
        return True
    except PermissionError:
        print(f"   [save] {os.path.basename(path)} is open (in Excel?) - "
              f"close it and re-run; skipped.")
    except Exception as exc:
        print(f"   [save] could not write {os.path.basename(path)}: {exc}")
    return False
