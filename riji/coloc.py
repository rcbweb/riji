# RB
r"""
===========================================================================
 coloc.py  —  colocalization for confocal .czi images   RB
===========================================================================
 Point it at a folder of images and it measures, for ANY number of dyes, how
 much every pair of dyes overlaps (colocalizes). It averages your replicates,
 runs quality-control + statistics, and writes tidy tables, charts, a matrix
 per condition, and a formatted Excel report -- and it saves a copy of itself
 next to the results, so every run is reproducible.

 HOW TO RUN
   1) once:  pip install czifile scikit-image scipy numpy matplotlib pandas openpyxl cellpose
   2) fill in the DYES block below, then double-click a terminal here and type:
        python coloc.py
      A folder-picker pops up. (Or:  python coloc.py "paste\your\folder\path" )
   3) to just list the dyes in a folder:   python coloc.py --dyes "your folder"
   4) brightest LIVE cells, N per folder:
         python -m cell_viability.rank_top "your folder" --top=20

 Every subfolder that holds .czi files is treated as one condition; every .czi
 inside is a replicate. Results land in a "coloc_results" folder inside the
 folder you point at.

 Rchin was here :)   (please keep this credit)
===========================================================================
"""
import os
import re
import sys
import glob
import math
import shutil
import platform
import datetime
import warnings
import xml.etree.ElementTree as ET
from itertools import combinations

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cell_viability import outputs as OUT  # noqa: E402
import coloc_outputs as CO                 # noqa: E402

import matplotlib
matplotlib.use("Agg")
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.colors import is_color_like, to_rgb
from matplotlib.patches import Circle
import czifile
from scipy.stats import pearsonr, ConstantInputWarning, kruskal, mannwhitneyu
from skimage.filters import threshold_otsu
from skimage.restoration import rolling_ball
from skimage.transform import resize

warnings.filterwarnings("ignore", category=ConstantInputWarning)

# ===========================================================================
#  ADVANCED ENGINE SETTINGS (GUI handles the rest)
# ===========================================================================

# Background Removal (rolling_ball recommended)
BG_METHOD = "rolling_ball"
BG_PERCENTILE = 10
ROLLING_BALL_RADIUS_UM = 10.0
ROLLING_BALL_SCALE = 4
FALLBACK_PIXEL_UM = 0.1

# Quality Control
QC_REVIEW_R_BELOW = -0.35
QC_MIN_SIGNAL_PIX = 50
CONTROL_HINT = "control"

# Outputs
OUTPUT_DIRNAME = "coloc_results"

# Strongest Cells Workflow
STRONGEST_MIN_AREA_UM2 = 5.0
STRONGEST_MAX_AREA_UM2 = 500000.0


# Morphometric Viability — Multi-Feature Physics-Based Scoring
# Each feature votes on whether a cell is dead or alive based on the
# optical physics of fluorophore redistribution during cell death.
# A weighted score from 0.0 (definitely live) to 1.0 (definitely dead)
# determines the classification.
FLAG_DEAD_BY_MORPHOLOGY = True
VIABILITY_DEAD_THRESHOLD = 0.60    # score above this = dead
VIABILITY_LIVE_THRESHOLD = 0.35    # score below this = live
                                   # in between = uncertain (discarded if strict)
STRICT_UNCERTAINTY_FILTER = False   # True = discard cells that fall between thresholds

# Circle colours on context images
CIRCLE_COLOR_LIVE = "lime"
CIRCLE_COLOR_DEAD = "red"

# When True, output is split into live/ and dead_flagged/ subfolders
SEPARATE_LIVE_DEAD = True

# Runtime settings -- the GUI overrides these while it runs; the defaults here
# also let the engine work on its own (from the command line, or from the
# archived coloc_USED.py) instead of crashing on an undefined name.
FOLDER = ""
MAX_CPUS = 8

USE_3D_VOLUMETRIC = False

DYES = []
COLORS = []
NOTES = []
THRESH_METHOD = "costes"          # "costes" (default) or "otsu"
SEGMENT_METHOD = "cellpose"       # "cellpose" (default) or "otsu"

# Costes settings. The threshold is found by bisection along the orthogonal
# regression line, the same way Fiji's Coloc_2 does it, so a number from this
# program can be checked against a number from Fiji.
# Which line the Costes cutoff slides down. This is a real choice with real
# consequences, so it is a setting rather than a hidden constant:
#   "rma"        - reduced major axis, slope sd(B)/sd(A). What this program has
#                  always used. Well behaved on weakly correlated images because
#                  the slope cannot run away, but it is blind to the covariance:
#                  channels correlated at 0.85 and at 0.30 get the same line.
#   "orthogonal" - perpendicular offsets, which is what Costes et al. 2004 and
#                  Fiji's Coloc_2 fit. Correct in principle and the right choice
#                  when the channels are strongly correlated, but its slope
#                  tends to (var_B - var_A)/cov, so on weakly correlated images
#                  the line turns near-vertical and its intercept goes strongly
#                  negative. Measured on Snap-138 (R = 0.34): slope 15.7,
#                  intercept -2183, so any cutoff below 139 demanded a NEGATIVE
#                  threshold on the other channel. Coloc_2 warns about exactly
#                  this. Threshold_below_zero is detected and reported.
# Both are reported either way: every row carries M from this rule and M from
# Otsu, so the reader can see how much the choice mattered on their own images.
COSTES_LINE = "rma"
COSTES_SCAN_STEPS = 200           # downward scan to bracket the FIRST crossing
COSTES_BISECTION_STEPS = 100      # then bisect inside the bracket for precision
COSTES_MIN_BELOW_PIX = 100        # need this many dim pixels to trust a correlation
COSTES_MAX_SIGNAL_FRAC = 0.50     # a cutoff calling over half the frame "signal"
                                  # has not separated anything; see below
# The Costes randomization test is OFF for whole frames, on purpose. It is not
# broken and it is not badly implemented -- it is saturated. It asks "could
# randomly rearranged pixels have produced this r", and the width of that null
# distribution shrinks as 1/sqrt(N). On a 1000x1000 frame that width is about
# 0.001, so an r of 0.02 clears it by twenty standard deviations and the answer
# is 100% for every image ever measured. Verified on all 11 images of the
# 20260722 set, at every block size from 2 to 64 px, controls included: 100%
# each time. A column of 100s is worse than no column, because it reads as
# confirmation. Enrichment below is the measure that does the job. Turn this
# back on for SMALL regions -- a single cell is 10^3-10^4 pixels, where the
# null has real width and the test has real power.
COSTES_SIGNIFICANCE_N = 0         # randomizations for whole frames; 0 = off
COSTES_SIGNIFICANCE_BLOCK = 8     # scramble in blocks this many pixels wide
COSTES_SIGNIFICANCE_MIN = 95.0    # below this percent, r is not above chance

# Enrichment: M divided by the M you would get from chance alone. Scale-free,
# unaffected by how many pixels the camera happened to have, and the number to
# read when asking whether an overlap means anything. 1.0 is exactly chance.
ENRICHMENT_WARN = 2.0             # below this, flagged as barely above chance;
                                  # a stated convention, not a law of nature
M_SPREAD_WARN = 0.15              # Costes and Otsu disagreeing by more than this
                                  # means the threshold, not the biology, is
                                  # setting M -- see the note in analyze()

# Cellpose tuning (only used when SEGMENT_METHOD == "cellpose")
CELLPOSE_GAUSSIAN_SIGMA = 3.0     # blur to merge speckles into continuous signal for cyto3
CELLPOSE_CELLPROB_THRESHOLD = -1.0  # moderately sensitive; -2 hallucinates, 0 misses faint cells
CELLPOSE_FLOW_THRESHOLD = 0.4     # default flow checking
CELLPOSE_DIAMETER = 0             # 0 = auto-detect diameter (handles multi-scale cells)
CELLPOSE_USE_BRIGHTFIELD = False  # True = segment from transmitted-light channel instead

# Multi-stage detection pipeline (catches missed cells, kills phantoms)
# Stage 1: Intensity-based pre-detection finds genuinely bright regions.
# Stage 2: Cellpose refines (splits touching cells) but ONLY within validated signal.
# Stage 3: Post-detection validation kills phantom circles.
INTENSITY_PREDETECT = True          # Enable fluorescence-aware pre-detection
LOCAL_BG_ANNULUS_UM = 15.0          # Radius (µm) of annular ring for local background estimation
MIN_LOCAL_SNR = 2.0                 # Region mean must be this many noise-stds above local bg
MIN_BRIGHT_PIXEL_FRACTION = 0.05    # At least 5% of region pixels must be individually "bright"
CELLPOSE_OVERLAP_THRESHOLD = 0.25   # Cellpose detection must overlap >=25% with intensity mask
ADAPTIVE_CLOSING_UM = 5.0           # Physical size (µm) for morphological closing — increased to bridge speckled dye patterns into whole-cell footprints
ADAPTIVE_SIGMA_UM = 3.0             # Physical size (µm) for Gaussian smoothing before detection — increased to merge punctate signals
SAVE_EXCEL = True
SAVE_PLOTS = True
SAVE_PANELS = True
# One figure per image, beside the montage. The montage compares
# folders; this is what you put a single field into a talk from.
# It reloads and redraws every image, which roughly triples the run, so it
# is off unless asked for. The montage and the every-image sheet come from
# one pass and are always written.
SAVE_EACH_IMAGE = False
STRONGEST_N_LIVE = "All"
STRONGEST_N_DEAD = "All"
STRONGEST_ROLE = ""
STRONGEST_CIRCLE = True
VIABILITY_ROLE = ""
EXPERIMENT_TITLE = ""

# ---------------------------------------------------------------------------
#  ENGINE   (you don't need to read past here)
# ---------------------------------------------------------------------------

_WARNED = set()


def _warn_once(key, msg):
    if key not in _WARNED:
        print(msg)
        _WARNED.add(key)


def _safe_write(path, writer):
    """Run writer(path); on a locked or failed file, warn clearly and keep going
    so one open file in Excel never discards a whole run's results."""
    try:
        writer(path)
        return True
    except PermissionError:
        print(f"  [save] '{os.path.basename(path)}' is open (in Excel?) -- close it and re-run; skipped.")
    except Exception as e:
        print(f"  [save] could not write {os.path.basename(path)}: {e}")
    return False


def _parse_map(lines, sep="="):
    """['a = b', ...] -> {'a': 'b'}. Blank / None / half-finished rows ignored.
    Warns if the same key is set twice (a stray leftover edit)."""
    out = {}
    for ln in lines:
        if ln is None:
            continue
        ln = str(ln).strip()
        if not ln or sep not in ln:
            continue
        k, v = (p.strip() for p in ln.split(sep, 1))
        if not k or not v or v.lower() == "none":
            continue
        if k in out and out[k] != v:
            _warn_once(f"dup:{k}", f"  [config] '{k}' is set more than once; using the last value ('{v}').")
        out[k] = v
    return out


_DYES = {}      # populated by GUI
_COLORS = {}    # populated by GUI
_NOTES = {}     # populated by GUI

# (role_a, role_b) rows from the window's comparison table. The
# comparisons named here are the ones the scientist came for; the rest
# are still computed and kept, just filed under Other permutations.
PAIRS_WANTED = []

# Compute every possible comparison, or only the ones asked for above.
#
# Three dyes make six comparisons and the example images reload every image
# once per comparison, so computing all six costs six passes over the data to
# answer one question. Off, only the requested comparisons are measured; with
# nothing requested everything is done, because otherwise the run would
# produce nothing at all.
COMPUTE_ALL_PAIRS = True


def note_for(a, b):
    """The user's note for a pair, or an automatic 'A inside B' label."""
    return _NOTES.get((a, b)) or f"{a} inside {b}"


def bad_colours():
    return [f"{r} = {c}" for r, c in _COLORS.items() if not is_color_like(c)]


def load_planes(path):
    """Read a .czi, .tif, or .nd2 as a (channels, Y, X) float array. Any Z-stack or time series
    is flattened by Maximum-Intensity Projection, so 3D data never crashes."""
    low = path.lower()
    
    if low.endswith((".tif", ".tiff")):
        import tifffile
        arr = tifffile.imread(path).astype(np.float64)
        if arr.ndim == 2:
            return arr[None]
        if arr.ndim == 3:
            # tifffile can read as (Y, X, C). Assume C is the smallest dimension if C <= 10.
            sizes = arr.shape
            min_dim = np.argmin(sizes)
            if sizes[min_dim] <= 10 and min_dim != 0:
                arr = np.moveaxis(arr, min_dim, 0)
    elif low.endswith(".nd2"):
        import nd2
        arr = nd2.imread(path).astype(np.float64)
    else:
        import czifile
        arr = np.squeeze(czifile.imread(path)).astype(np.float64)

    if arr.ndim == 2:
        return arr[None]                      # single channel, single plane
    if arr.ndim == 3:
        return arr                            # (channels, Y, X) -- the usual case
        
    if USE_3D_VOLUMETRIC:
        return arr
        
    _warn_once("mip", "  [note] a Z-stack/time-series was flattened by max-intensity projection. "
                      "On THICK samples this can create FALSE colocalization (objects separated "
                      "in z fall on one pixel). Fine for flat adherent cells.")
    sizes = arr.shape
    yx = sorted(sorted(range(arr.ndim), key=lambda a: sizes[a])[-2:])
    rem = [a for a in range(arr.ndim) if a not in yx]
    nC = len(channel_info(path))
    cax = next((a for a in rem if sizes[a] == nC), rem[0])
    arr = np.moveaxis(arr, [cax, yx[0], yx[1]], [0, -2, -1])   # -> (C, ..., Y, X)
    target_ndim = 3
    while arr.ndim > target_ndim:
        arr = arr.max(axis=1)                 # collapse Z / time by MIP
    return arr


def channel_info(path):
    """One dict per channel from the metadata: index, name, fluor (dye),
    em (emission nm), detector, transmitted (True/False)."""
    low = path.lower()
    chans = []

    if low.endswith(".nd2"):
        try:
            import nd2
            with nd2.ND2File(path) as f:
                for i, ch in enumerate(f.metadata.channels):
                    name = ch.channel.name
                    em = ch.channel.emissionLambdaNm
                    chans.append(dict(index=i, name=name, fluor=name, em=em,
                                      detector="", transmitted=False))
            if chans: return chans
        except Exception: pass

    elif low.endswith(".czi"):
        try:
            import czifile
            import xml.etree.ElementTree as ET
            root = ET.fromstring(czifile.CziFile(path).metadata())
            for i, ch in enumerate(root.findall(".//Dimensions/Channels/Channel")):
                def g(tag):
                    e = ch.find(tag)
                    return e.text if e is not None else None
                det = ch.find(".//DetectorSettings/Detector")
                det_id = det.get("Id") if det is not None else ""
                fluor = g("Fluor")
                em = g("EmissionWavelength")
                try: em = float(em) if em else None
                except ValueError: em = None
                det_u = (det_id or "").upper()
                is_tl = ("ESID" in det_u) or ("T-PMT" in det_u) or ("TPMT" in det_u) or det_u.endswith("TL")
                unknown = (not det_id) and (fluor is None) and (em is None)
                transmitted = is_tl or unknown
                chans.append(dict(index=i, name=ch.get("Name"), fluor=fluor, em=em,
                                  detector=det_id, transmitted=transmitted))
            if chans: return chans
        except Exception: pass

    # Fallback for generic TIF or if metadata fails
    nC = 1
    if low.endswith((".tif", ".tiff")):
        try:
            import tifffile
            with tifffile.TiffFile(path) as tif:
                s = tif.series[0].shape
                nC = min(s) if len(s) == 3 else (s[0] if len(s) > 3 else 1)
        except Exception: pass

    for i in range(nC):
        chans.append(dict(index=i, name=f"Channel {i+1}", fluor=f"Channel {i+1}", em=None,
                          detector="", transmitted=False))
    return chans


def pixel_size_um(path):
    """Physical size of one pixel in microns (from metadata); None if absent."""
    low = path.lower()
    try:
        if low.endswith(".nd2"):
            import nd2
            with nd2.ND2File(path) as f:
                return float(f.voxel_size().x)
        elif low.endswith(".czi"):
            import czifile
            import xml.etree.ElementTree as ET
            root = ET.fromstring(czifile.CziFile(path).metadata())
            v = root.find(".//Scaling/Items/Distance[@Id='X']/Value")
            return float(v.text) * 1e6
        elif low.endswith((".tif", ".tiff")):
            # Tifffile might have Resolution tags, but it's often complex (e.g., cm vs inches).
            # We return None so it falls back to FALLBACK_PIXEL_UM
            pass
    except Exception:
        pass
    return None


# The dyes this app knows by name. Key = how it appears in a channel name
# (lowercase, matched as a substring); value = (what it marks, emission colour).
#
# Used for the Dye Guide, the reference sheet and the colour a role gets when
# none was chosen. It is documentation, not identification: which channel is
# which dye is decided by role_of() from what YOU type in the Dyes box, so an
# entry here can never silently relabel a measurement.
#
# Grouped by emission, roughly blue -> far-red, because that is how a filter
# set is laid out and how people look for one.
DYE_GUIDE = {
    # ---- ultraviolet / blue emission --------------------------------------
    "dapi":            ("nuclear stain (DNA)", "blue"),
    "hoechst":         ("nuclear stain (DNA), enters live cells", "blue"),
    "hoechst 33342":   ("nuclear stain (DNA), live-cell permeant", "blue"),
    "hoechst 33258":   ("nuclear stain (DNA), fixed cells", "blue"),
    "pacific blue":    ("blue dye", "blue"),
    "alexa 350":       ("blue dye", "blue"),
    "alexa 405":       ("blue-violet dye", "blue"),
    "dylight 405":     ("blue-violet dye", "blue"),
    "atto 390":        ("blue dye", "blue"),
    "coumarin":        ("blue dye", "blue"),
    "bfp":             ("blue fluorescent protein", "blue"),
    "ebfp":            ("blue fluorescent protein", "blue"),
    "mtagbfp":         ("blue fluorescent protein", "blue"),
    "mbfp":            ("blue fluorescent protein", "blue"),
    "fura-2":          ("ratiometric calcium indicator", "blue"),
    "indo-1":          ("ratiometric calcium indicator", "blue"),
    "lysosensor blue": ("acidic organelle pH indicator", "blue"),
    "calcein blue":    ("live-cell viability stain", "blue"),

    # ---- cyan --------------------------------------------------------------
    "cfp":             ("cyan fluorescent protein", "cyan"),
    "ecfp":            ("cyan fluorescent protein", "cyan"),
    "cerulean":        ("cyan fluorescent protein", "cyan"),
    "mturquoise":      ("cyan fluorescent protein", "cyan"),
    "atto 425":        ("cyan dye", "cyan"),

    # ---- green emission ----------------------------------------------------
    "fitc":            ("green dye, commonly on dextran cargo", "green"),
    "fluorescein":     ("green dye", "green"),
    "alexa 488":       ("green dye", "green"),
    "alexa 514":       ("yellow-green dye", "green"),
    "cy2":             ("green dye", "green"),
    "atto 488":        ("green dye", "green"),
    "dylight 488":     ("green dye", "green"),
    "cf488":           ("green dye", "green"),
    "gfp":             ("green fluorescent protein", "green"),
    "egfp":            ("green fluorescent protein", "green"),
    "mneongreen":      ("bright green fluorescent protein", "green"),
    "mgreenlantern":   ("bright green fluorescent protein", "green"),
    "yfp":             ("yellow-green fluorescent protein", "green"),
    "eyfp":            ("yellow-green fluorescent protein", "green"),
    "venus":           ("yellow-green fluorescent protein", "green"),
    "citrine":         ("yellow-green fluorescent protein", "green"),
    "bodipy fl":       ("green lipid / membrane probe", "green"),
    "bodipy 493":      ("neutral lipid droplet stain", "green"),
    "nbd":             ("green lipid probe", "green"),
    "calcein":         ("live-cell viability stain (esterase activity)", "green"),
    "calcein am":      ("live-cell viability stain (esterase activity)", "green"),
    "cmfda":           ("whole-cell live green stain", "green"),
    "celltracker green": ("whole-cell live green stain", "green"),
    "syto 9":          ("nucleic acid stain, live cells", "green"),
    "sytox green":     ("dead-cell nuclear stain", "green"),
    "fluo-4":          ("calcium indicator", "green"),
    "fluo-3":          ("calcium indicator", "green"),
    "gcamp":           ("genetically encoded calcium indicator", "green"),
    "mitotracker green": ("MITOCHONDRIA", "green"),
    "lysotracker green": ("LYSOSOMES / acidic organelles", "green"),
    "er-tracker green":  ("endoplasmic reticulum", "green"),
    "phalloidin":      ("F-actin (filamentous actin)", "green"),
    "wga":             ("wheat germ agglutinin: membrane glycans", "green"),
    "dio":             ("lipophilic membrane label", "green"),
    "acridine orange": ("acidic organelles / nucleic acid", "green"),
    "annexin v":       ("apoptosis marker (exposed phosphatidylserine)", "green"),

    # ---- yellow / orange emission ------------------------------------------
    "cy3":             ("orange-red dye, often the vesicle / particle label", "orange"),
    "tritc":           ("orange-red dye", "orange"),
    "rhod":            ("rhodamine / Rhod-PE, orange lipid label", "orange"),
    "rhodamine":       ("orange-red dye", "orange"),
    "rhodamine 123":   ("MITOCHONDRIA, membrane-potential dependent", "orange"),
    "alexa 546":       ("orange-red dye", "orange"),
    "alexa 555":       ("orange-red dye", "orange"),
    "alexa 568":       ("orange-red dye", "orange"),
    "atto 550":        ("orange dye", "orange"),
    "atto 565":        ("orange dye", "orange"),
    "dylight 550":     ("orange dye", "orange"),
    "cf568":           ("orange dye", "orange"),
    "morange":         ("orange fluorescent protein", "orange"),
    "mko":             ("Kusabira Orange fluorescent protein", "orange"),
    "tmrm":            ("MITOCHONDRIA, membrane-potential dependent", "orange"),
    "tmre":            ("MITOCHONDRIA, membrane-potential dependent", "orange"),
    "jc-1":            ("mitochondrial membrane potential (green/red shift)", "orange"),
    "mitotracker orange": ("MITOCHONDRIA", "orange"),
    "cmtmros":         ("MITOCHONDRIA, MitoTracker Orange", "orange"),
    "lysotracker yellow": ("LYSOSOMES / acidic organelles", "orange"),
    "dii":             ("lipophilic membrane label", "orange"),
    "nile red":        ("neutral lipid droplets", "orange"),
    "fm 1-43":         ("membrane / endocytosis tracer", "orange"),

    # ---- red emission ------------------------------------------------------
    "texas red":       ("red dye", "red"),
    "alexa 594":       ("red dye", "red"),
    "alexa 610":       ("red dye", "red"),
    "mcherry":         ("red fluorescent protein", "red"),
    "mrfp":            ("red fluorescent protein", "red"),
    "dsred":           ("red fluorescent protein", "red"),
    "tdtomato":        ("red fluorescent protein", "red"),
    "mruby":           ("red fluorescent protein", "red"),
    "mscarlet":        ("bright red fluorescent protein", "red"),
    "propidium iodide": ("dead-cell nuclear stain", "red"),
    "pi":              ("dead-cell nuclear stain (propidium iodide)", "red"),
    "7-aad":           ("dead-cell nuclear stain", "red"),
    "ethidium homodimer": ("dead-cell nuclear stain", "red"),
    "mitotracker red": ("MITOCHONDRIA", "red"),
    "cmxros":          ("MITOCHONDRIA, MitoTracker Red CMXRos", "red"),
    "lysotracker red": ("LYSOSOMES / acidic organelles", "red"),
    "er-tracker red":  ("endoplasmic reticulum", "red"),
    "fm 4-64":         ("membrane / endocytosis tracer", "red"),
    "lipidtox":        ("neutral lipid droplets", "red"),
    "phrodo red":      ("pH-sensitive: bright once inside an acidic endosome", "red"),

    # ---- far-red / deep-red emission ---------------------------------------
    "deep red":        ("far-red stain", "far-red"),
    "far red":         ("far-red stain", "far-red"),
    "cy5":             ("far-red dye", "far-red"),
    "cy5.5":           ("near-infrared dye", "far-red"),
    "cy7":             ("near-infrared dye", "far-red"),
    "alexa 633":       ("far-red dye", "far-red"),
    "alexa 647":       ("far-red dye", "far-red"),
    "alexa 660":       ("far-red dye", "far-red"),
    "alexa 680":       ("near-infrared dye", "far-red"),
    "alexa 700":       ("near-infrared dye", "far-red"),
    "alexa 750":       ("near-infrared dye", "far-red"),
    "atto 647n":       ("far-red dye, common in STED", "far-red"),
    "atto 655":        ("far-red dye", "far-red"),
    "dylight 650":     ("far-red dye", "far-red"),
    "cf640r":          ("far-red dye", "far-red"),
    "apc":             ("allophycocyanin, far-red", "far-red"),
    "draq5":           ("far-red nuclear stain, live cells", "far-red"),
    "draq7":           ("far-red nuclear stain, dead cells only", "far-red"),
    "to-pro-3":        ("far-red dead-cell nuclear stain", "far-red"),
    "sytox red":       ("far-red dead-cell nuclear stain", "far-red"),
    "sir-actin":       ("far-red live-cell F-actin stain", "far-red"),
    "sir-tubulin":     ("far-red live-cell microtubule stain", "far-red"),
    "sir-dna":         ("far-red live-cell nuclear stain", "far-red"),
    "sir-lysosome":    ("LYSOSOMES, far-red live-cell stain", "far-red"),
    "mitotracker deep red": ("MITOCHONDRIA", "far-red"),
    "lysotracker deep red": ("LYSOSOMES / acidic organelles", "far-red"),
    "cellmask":        ("whole-cell / plasma membrane stain", "far-red"),
    "did":             ("lipophilic membrane label", "far-red"),
    "dir":             ("near-infrared lipophilic membrane label", "far-red"),
    "irfp":            ("near-infrared fluorescent protein", "far-red"),
    "mirfp":           ("near-infrared fluorescent protein", "far-red"),
    "phrodo deep red": ("pH-sensitive: bright inside an acidic endosome", "far-red"),

    # ---- cargo / generic ---------------------------------------------------
    "dextran":         ("sugar cargo, usually FITC- or Alexa-labelled", "-"),
    "transferrin":     ("receptor-mediated endocytosis cargo", "-"),
    "egf":             ("receptor-mediated endocytosis cargo", "-"),
    "ldl":             ("lipoprotein endocytosis cargo", "-"),

    # ---- family names on their own -----------------------------------------
    # A channel is often recorded as just the family - "LysoTracker", "Alexa
    # 647", "MitoTracker" - with no colour after it. Listing only the
    # colour-specific variants meant those matched nothing at all.
    "lysotracker":     ("LYSOSOMES / acidic organelles", "red / deep-red"),
    "lysosensor":      ("acidic organelle pH indicator", "blue / green"),
    "mitotracker":     ("MITOCHONDRIA", "green / red / far-red"),
    "er-tracker":      ("endoplasmic reticulum", "green / red"),
    "ertracker":       ("endoplasmic reticulum", "green / red"),
    "golgi":           ("Golgi apparatus marker", "green / red"),
    "bodipy":          ("lipid / membrane probe", "green"),
    "celltracker":     ("whole-cell live stain", "various"),
    "cellmask":        ("whole-cell / plasma membrane stain", "various"),
    "alexa":           ("Alexa Fluor dye - the number gives the colour", "various"),
    "atto":            ("ATTO dye - the number gives the colour", "various"),
    "dylight":         ("DyLight dye - the number gives the colour", "various"),
    "syto":            ("nucleic acid stain", "various"),
    "sytox":           ("dead-cell nuclear stain", "various"),
    "phrodo":          ("pH-sensitive: bright inside an acidic endosome", "various"),
    "sir-":            ("SiR far-red live-cell probe", "far-red"),
    "phalloidin":      ("F-actin (filamentous actin)", "various"),
    "quantum dot":     ("QDot - the number gives the emission", "various"),
    "qdot":            ("QDot - the number gives the emission", "various"),

    # ---- not dyes ----------------------------------------------------------
    "esid":            ("transmitted light detector (NOT a dye)", "-"),
    "t-pmt":           ("transmitted light detector (NOT a dye)", "-"),
    "brightfield":     ("transmitted light (NOT a dye)", "-"),
    "dic":             ("transmitted light, Nomarski (NOT a dye)", "-"),
}

DYE_SYNONYMS = {
    "rhod": ["cy3", "tritc", "rhodamine"],
    "cy3": ["rhod", "tritc", "rhodamine"],
    "tritc": ["rhod", "cy3", "rhodamine"],
    "fitc": ["alexa 488", "gfp", "fluorescein"],
    "gfp": ["fitc", "alexa 488", "fluorescein"],
    "alexa 488": ["fitc", "gfp", "fluorescein"],
    "dapi": ["hoechst"],
    "hoechst": ["dapi"],
}


def _dye_norm(s):
    """A dye name in a comparable form.

    Catalogues write "Alexa 647"; microscopes write "Alexa Fluor 647", and
    sometimes "Alexa Fluor Plus 647". The brand words carry no information
    about which dye it is, so they come out. Punctuation is levelled too, so
    "TO-PRO-3", "TO PRO 3" and "topro3" are one thing.

    "fluor" is removed only as a whole word - fluorescein must survive.
    """
    s = (s or "").lower()
    s = re.sub(r"\b(fluor|plus|dye|conjugate|labelled|labeled)\b", " ", s)
    s = re.sub(r"[^a-z0-9.]+", " ", s)
    return " ".join(s.split())


def describe_dye(name):
    """What a channel's dye is, if the catalogue knows it.

    Longest key wins. With a catalogue this size the short names are
    substrings of the long ones - "cy5" inside "cy5.5", "mitotracker" inside
    "mitotracker deep red" - and taking the first match would describe the
    wrong dye, which for MitoTracker against LysoTracker means naming the
    wrong organelle.
    """
    low = _dye_norm(name)
    if not low:
        return None
    best = None
    for key, desc in DYE_GUIDE.items():
        k = _dye_norm(key)
        if k and k in low and (best is None or len(k) > len(best[0])):
            best = (k, desc)
    return best[1] if best else None


# How close a typed dye name has to be to a channel's name before it is
# accepted as the same dye. High on purpose.
#
# It has to be loose enough to forgive how a dye gets written - "LysoTracker",
# "Lyso Tracker", "lysotracker deep red 633", a dropped letter - and tight
# enough that it never crosses from one dye to another. LysoTracker against
# MitoTracker scores about 0.73: they share "otracker" and nothing else that
# matters. They stain different organelles, so quietly treating one as the
# other would put the word "Lysosome" on a measurement of mitochondria, and
# no amount of convenience is worth that. Below the threshold nothing is
# guessed; the run says what it found and lets the scientist decide.
NEAR_MATCH_RATIO = 0.85


def _dye_key(s):
    """A dye name reduced to its comparable core.

    Digits are KEPT. Dropping them made "Cy3" and "Cy5" both collapse to "cy"
    and match perfectly - which would have reported the lysosome channel as
    the vesicle one. The number is most of the name in that family.
    """
    return "".join(c for c in str(s).lower() if c.isalnum())


def _numbers(s):
    """The number groups in a dye name: {'488'}, {'3'}, {'33342'}."""
    import re
    return set(re.findall(r"\d+", str(s)))


def _near_match(typed, channel_name):
    """How closely a typed dye name matches a channel's, 0..1.

    Compared against the whole channel name AND each word in it, because a
    channel is usually called "LysoTracker Deep Red 633" while people type
    "lysotracker" - against the whole string that scores badly, against the
    first word it scores 1.0.

    Where BOTH names carry a number and the numbers disagree, they are
    different dyes and nothing else matters. Alexa 488 and Alexa Fluor 647
    share every letter that appears in both, so on text alone they looked
    identical; the number is the whole of what separates them, and the same
    goes for Cy3 against Cy5.
    """
    from difflib import SequenceMatcher
    t = _dye_key(typed)
    if not t:
        return 0.0
    nt, nc = _numbers(typed), _numbers(channel_name)
    if nt and nc and not (nt & nc):
        return 0.0

    cands = [_dye_key(channel_name)]
    cands += [_dye_key(w) for w in str(channel_name).replace("-", " ").split()]
    best = 0.0
    for c in cands:
        if not c:
            continue
        if t in c or c in t:
            return 1.0
        best = max(best, SequenceMatcher(None, t, c).ratio())
    return best


def _chan_name(chan):
    return ((chan.get("fluor") or chan.get("name") or "").strip()
            or f"channel {chan.get('index')}")


def resolve_roles(chans, report=None):
    """Decide every channel's role together, not one channel at a time.

    A dye line whose name matches no channel used to leave that channel under
    whatever the microscope recorded - so "Lysotracker = Lysosome" against a
    channel the scope called "MitoTracker Deep Red 633" produced results
    labelled MitoTracker, and the role the scientist asked for never appeared.

    When every OTHER line has found its channel and exactly one line and one
    channel are left over, there is only one assignment left to make. That is
    not a guess about which dye is in the tube: it is the arrangement the
    scientist described by listing those dyes, with everything else already
    accounted for. The name they chose is the name they get.

    It only fires when the leftovers are unambiguous - one and one. Two
    unmatched lines against two unmatched channels could be paired either way
    round, and picking one silently is how a lysosome becomes a mitochondrion.
    Those are reported and left alone.
    """
    matched = {c["index"]: _match_role(c, report) for c in chans}
    roles = {c["index"]: (matched[c["index"]] or _chan_name(c)) for c in chans}

    used = {str(m).strip().lower() for m in matched.values() if m}
    spare_lines = [(d, r) for d, r in _DYES.items()
                   if str(r).strip().lower() not in used]
    spare_chans = [c for c in chans if not matched[c["index"]]]

    if len(spare_lines) == 1 and len(spare_chans) == 1:
        dye, role = spare_lines[0]
        chan = spare_chans[0]
        roles[chan["index"]] = role
        if report is not None:
            report.append(("only one left", _chan_name(chan), dye, role))
        spare_lines, spare_chans = [], []
    return roles, spare_lines, spare_chans


def role_of(chan, report=None):
    """One channel's role. resolve_roles() is the one that sees them all."""
    return _match_role(chan, report) or _chan_name(chan)


def _match_role(chan, report=None):
    """Channel -> role: the DYES entry whose name matches (MOST SPECIFIC / longest
    match wins, so 'Cy3' doesn't get shadowed by a shorter 'Cy'), else the dye's
    own name (whitespace-trimmed so replicates don't fragment)."""
    raw = (chan["fluor"] or chan["name"] or "")
    name = raw.lower()
    best = None
    for dye, role in _DYES.items():
        dye_lower = dye.lower()
        if dye_lower in name:
            if best is None or len(dye_lower) > len(best[0]):
                best = (dye_lower, role)
        elif dye_lower in DYE_SYNONYMS:
            for syn in DYE_SYNONYMS[dye_lower]:
                if syn in name:
                    if best is None or len(syn) > len(best[0]):
                        best = (syn, role)
    if best:
        return best[1]

    # Nothing matched outright. Try again allowing for how the same dye gets
    # written differently, and take the closest - but only if it is close.
    # pylint: disable=too-many-nested-blocks
    near, score = None, 0.0
    for dye, role in _DYES.items():
        r = _near_match(dye, raw)
        if r > score:
            near, score = (dye, role), r
    if near and score >= NEAR_MATCH_RATIO:
        if report is not None:
            report.append(("close enough", raw, near[0], near[1]))
        return near[1]

    return None


def color_for(role, em):
    """Your chosen colour for a role; if it isn't valid, derive one from wavelength."""
    c = _COLORS.get(role)
    if c and is_color_like(c):
        return c
    if em is None:
        return "gray"
    return ("blue" if em < 500 else "green" if em < 560 else
            "gold" if em < 600 else "orange" if em < 650 else "magenta")




def _detect_fluorescent_regions(raw_flat, px):
    """Stage 1: Intensity-based pre-detection.
    Finds regions of the image that are genuinely bright — what a human scientist
    sees as 'obvious cell signal' regardless of shape. Uses multi-scale adaptive
    thresholding so it catches both small punctate and large spread-out cells.

    Returns a binary mask where True = genuine fluorescent signal."""
    from scipy.ndimage import gaussian_filter, binary_fill_holes, binary_dilation, uniform_filter
    from skimage.morphology import remove_small_objects

    # Physical-size-aware kernel sizes
    close_px = max(3, int(round(ADAPTIVE_CLOSING_UM / px)))
    sigma_px = max(1.0, ADAPTIVE_SIGMA_UM / px)

    # --- Multi-scale detection ---
    # Scale 1: Fine — catches small bright objects (sigma ~ 1.5µm)
    smooth_fine = gaussian_filter(raw_flat, sigma=sigma_px)
    # Scale 2: Coarse — catches large spread cells (sigma ~ 5µm)
    smooth_coarse = gaussian_filter(raw_flat, sigma=sigma_px * 3.5)

    # Adaptive local threshold: a pixel is "bright" if it exceeds its local
    # neighbourhood mean by a significant margin. Window = 30µm (large enough
    # to capture background around any cell).
    window_px = max(31, int(round(30.0 / px)) | 1)  # ensure odd
    local_mean = uniform_filter(raw_flat, size=window_px)
    # Estimate local noise as the standard deviation in the neighbourhood
    local_sq_mean = uniform_filter(raw_flat ** 2, size=window_px)
    local_std = np.sqrt(np.maximum(local_sq_mean - local_mean ** 2, 0)) + 1e-9

    # A pixel is signal if it's > local_mean + 2*local_std at EITHER scale
    threshold_map = local_mean + MIN_LOCAL_SNR * local_std
    mask_fine = smooth_fine > threshold_map
    mask_coarse = smooth_coarse > threshold_map
    combined = mask_fine | mask_coarse

    # Also add a global Otsu pass to catch cells in very sparse images where
    # the local threshold might be too conservative
    try:
        from skimage.filters import threshold_otsu as _otsu
        global_thresh = _otsu(raw_flat)
        # Only use Otsu if it's actually selective (above the median)
        if global_thresh > np.median(raw_flat):
            combined = combined | (raw_flat > global_thresh)
    except Exception:
        pass

    # Morphological cleanup: bridge nearby speckles (punctate dye uptake),
    # fill holes, remove dust
    from scipy.ndimage import grey_closing
    # Close small gaps — physical size aware
    struct_size = max(3, close_px * 2 + 1)
    combined = binary_dilation(combined, structure=np.ones((close_px, close_px)), iterations=2)
    combined = binary_fill_holes(combined)
    # Remove tiny noise specks (< 50 pixels or < ~0.5µm²)
    min_obj_px = max(30, int(0.5 / (px * px)))
    try:
        combined = remove_small_objects(combined, min_size=min_obj_px)
    except Exception:
        pass

    return combined


def _validate_detection(r, raw_flat, px, global_bg, image_noise_std):
    """Stage 3: Post-detection validation.
    Checks whether a single detected region contains genuine fluorescent signal
    or is a phantom (noise artefact that Cellpose hallucinated into a 'cell').

    Returns True if the region is VALID (keep), False if it's a phantom (discard)."""
    yy, xx = r.coords[:, 0], r.coords[:, 1]
    vals = raw_flat[yy, xx]
    mean_int = float(vals.mean())

    # 1. LOCAL BACKGROUND CHECK
    # Instead of comparing to the global 10th percentile (which is too loose),
    # compare to the annular neighbourhood immediately around this region.
    min_r, min_c, max_r, max_c = r.bbox
    annulus_px = max(10, int(round(LOCAL_BG_ANNULUS_UM / px)))
    # Expand bbox to get the annular region
    a_min_r = max(0, min_r - annulus_px)
    a_min_c = max(0, min_c - annulus_px)
    a_max_r = min(raw_flat.shape[0], max_r + annulus_px)
    a_max_c = min(raw_flat.shape[1], max_c + annulus_px)
    # Get all pixels in the expanded box
    annulus_patch = raw_flat[a_min_r:a_max_r, a_min_c:a_max_c]
    # Create a mask that excludes the cell itself (we want JUST the surrounding area)
    annulus_mask = np.ones(annulus_patch.shape, dtype=bool)
    # Map cell bbox into the patch coordinates
    cell_r0 = min_r - a_min_r
    cell_c0 = min_c - a_min_c
    cell_r1 = max_r - a_min_r
    cell_c1 = max_c - a_min_c
    annulus_mask[cell_r0:cell_r1, cell_c0:cell_c1] = False
    local_bg_vals = annulus_patch[annulus_mask]

    if len(local_bg_vals) > 20:
        local_bg = float(np.median(local_bg_vals))
        local_std = float(np.std(local_bg_vals)) + 1e-9
    else:
        # Fallback to global if annulus is too small
        local_bg = global_bg
        local_std = image_noise_std

    # Region mean must be significantly above local background
    local_snr = (mean_int - local_bg) / local_std
    if local_snr < MIN_LOCAL_SNR:
        return False

    # 2. BRIGHT PIXEL FRACTION CHECK
    # A real cell has concentrated signal — at least some of its pixels should be
    # individually bright. A phantom is diffuse noise that happens to average
    # slightly above background.
    bright_threshold = local_bg + 3.0 * local_std
    bright_fraction = float((vals > bright_threshold).mean())
    if bright_fraction < MIN_BRIGHT_PIXEL_FRACTION:
        return False

    # 3. PEAK INTENSITY CHECK
    # The brightest pixel in a real cell should stand out clearly from background
    peak = float(vals.max())
    if peak < local_bg + 4.0 * local_std:
        return False

    return True


def subtract_bg(ch, radius_px):
    """Background subtraction; clips negatives to zero. radius_px is the rolling
    ball radius already converted from microns to pixels for this image."""
    if BG_METHOD == "rolling_ball":
        s = max(1, ROLLING_BALL_SCALE)
        small_idx = tuple(slice(None, None, s) for _ in range(ch.ndim))
        small = ch[small_idx]
        bg_small = rolling_ball(small, radius=max(3, int(radius_px // s)))
        bg = resize(bg_small, ch.shape, order=1, preserve_range=True)
    else:
        bg = np.percentile(ch, BG_PERCENTILE)
    out = ch - bg
    out[out < 0] = 0
    return out


def otsu_mask(im):
    if im.max() <= im.min():
        return np.zeros(im.shape, bool)
    return im > threshold_otsu(im)


def costes_regression(A, B, line=None):
    """The line the Costes cutoff slides along. See COSTES_LINE for which is
    fitted and why the choice matters.

    A comment here used to call the RMA line "the canonical Costes choice". It
    is not -- Costes et al. 2004 and Coloc_2 fit perpendicular offsets -- and
    the difference is not cosmetic: the RMA slope is sd(B)/sd(A) whatever the
    covariance is, so channels correlated at 0.85 and at 0.30 get the identical
    line. But the orthogonal line is not simply the better one to use. On
    weakly correlated images its slope runs away and its intercept goes
    negative, and a negative intercept means the cutoff it derives for the
    second channel drops below zero, which no image can satisfy. Which line to
    fit is therefore a setting, not a fact.

    Returns (slope, intercept), or None when the channels have no positive
    relation at all and the whole construction is meaningless.
    """
    line = (COSTES_LINE if line is None else line).lower()
    av, bv = np.asarray(A).ravel(), np.asarray(B).ravel()
    if av.size < 2:
        return None
    va, vb = av.var(), bv.var()
    if va <= 0 or vb <= 0:
        return None
    cov = float(np.mean((av - av.mean()) * (bv - bv.mean())))
    if cov <= 0:
        # Costes walks DOWN a positively sloped line until the dim pixels stop
        # correlating. With cov <= 0 there is no such line: the dim pixels never
        # correlated, so there is no point to stop at. Say so instead of
        # returning a line and pretending.
        return None
    if line == "orthogonal":
        m = ((vb - va) + math.sqrt((vb - va) ** 2 + 4.0 * cov ** 2)) / (2.0 * cov)
    else:
        m = math.sqrt(vb / va)
    b = float(bv.mean() - m * av.mean())
    return float(m), b


def costes_thresholds(A, B):
    """Costes thresholds for a pair, by bisection along the orthogonal
    regression line -- the cutoff slides down until the pixels below it stop
    being positively correlated, i.e. until what is left below looks like noise.

    Returns (tA, tB, info). `info["status"]` is one of:
        "ok"           - the search converged on a crossing
        "no_relation"  - cov <= 0; Costes does not apply to this pair
        "no_crossing"  - slid to the floor without the correlation ever dying
        "flat"         - a channel has no variation
    Only "ok" is a threshold anyone should report a Manders number from. The
    search used to run 200 evenly spaced steps from max to min and, if it never
    found a crossing, quietly return the FLOOR -- a threshold of A.min(), which
    passes every pixel and makes M a number about nothing. That silent floor is
    now "no_crossing", and the caller falls back to Otsu and records that it did.
    """
    A = np.asarray(A)
    B = np.asarray(B)
    info = {"status": "flat", "slope": float("nan"), "intercept": float("nan"),
            "r_below": float("nan")}
    if A.size == 0 or A.max() <= A.min() or B.max() <= B.min():
        return float(A.min()) if A.size else 0.0, \
               float(B.min()) if B.size else 0.0, info

    fit = costes_regression(A, B)
    if fit is None:
        info["status"] = "no_relation"
        return float(A.min()), float(B.min()), info
    m, b = fit
    info["slope"], info["intercept"] = m, b

    # A steep line with a strongly negative intercept asks for a cutoff below
    # zero on the second channel, which no image can satisfy, so from there
    # down every pixel counts as positive and the search parks against that
    # wall. Coloc_2 raises the same complaint. There is no need to predict it
    # from the intercept, though -- an intercept below B's floor is ordinary,
    # since the line has to pass through the bottom of the data. What matters
    # is only where the search actually stops, so it is caught below, on the
    # cutoff that comes out, by asking whether it separates anything.

    def r_below(ta):
        """Pearson r of the pixels at or below the cutoff. None = too few."""
        below = (A <= ta) & (B <= m * ta + b)
        n = int(below.sum())
        if n < COSTES_MIN_BELOW_PIX:
            return None
        sub_a, sub_b = A[below], B[below]
        if sub_a.max() <= sub_a.min() or sub_b.max() <= sub_b.min():
            return None
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ConstantInputWarning)
            r = pearsonr(sub_a, sub_b)[0]
        return r if np.isfinite(r) else None

    # Costes' rule is the FIRST place, coming down from the top, where the dim
    # pixels stop correlating. Pure bisection cannot be used to find it: it
    # assumes r crosses zero once, and on real images r wobbles across zero
    # more than once, so bisection can converge on a lower crossing than the
    # first one and hand back a cutoff near the image floor. That is not
    # hypothetical -- it drove a strongly colocalized pair (R = 0.77) to a
    # threshold that kept 100% of the frame.
    #
    # So: scan downward to BRACKET the first crossing, then bisect inside that
    # bracket for precision. The scan gets the right root, the bisection gets
    # the right value -- where a fixed 200-step scan alone could only land on
    # multiples of ~330 grey levels on a 16-bit image.
    # Scan by PERCENTILE of A, not by evenly spaced intensity. A fluorescence
    # frame is mostly dim with a few very bright pixels, so an even scan across
    # the raw range spends nearly all its steps in empty space above the data
    # and strides over the interesting part in chunks of a few hundred grey
    # levels. On Snap-138 the crossing sits near 60-100 out of a 65535 range:
    # an even 200-step scan jumps 330 at a time and misses it completely,
    # reporting "no crossing" for an image that plainly has one. Percentile
    # spacing puts the steps where the pixels are.
    lo_a, hi_a = float(A.min()), float(A.max())
    tol = (hi_a - lo_a) / 1e6
    best = None
    prev = hi_a
    cand = np.percentile(A, np.linspace(99.9, 0.0, COSTES_SCAN_STEPS))
    cand = np.unique(cand)[::-1]           # descending, no repeats
    for ta in cand:
        r = r_below(ta)
        if r is None:
            prev = ta
            continue
        if r <= 0:                         # bracketed: crossing in (ta, prev]
            lo, hi = ta, prev
            for _ in range(COSTES_BISECTION_STEPS):
                mid = 0.5 * (lo + hi)
                rm = r_below(mid)
                if rm is None or rm <= 0:
                    lo = mid               # still past the crossing -> go up
                else:
                    hi = mid               # still correlated -> come down
                if hi - lo < tol:
                    break
            rl = r_below(lo)
            best = (lo, rl if rl is not None else float(r))
            break
        prev = ta

    if best is None:
        info["status"] = "no_crossing"
        return float(A.min()), float(B.min()), info
    ta, r = best
    tb = m * ta + b
    info["r_below"] = float(r)

    # A cutoff that calls most of the frame "signal" has not separated anything,
    # whatever fitted it. This is not hypothetical: on Snap-138 (R = 0.34) the
    # line put the vesicle cutoff at 14 grey levels, which is below the image's
    # own median -- 95% of the frame counted as vesicle-positive, and M came out
    # 0.98 because almost every lysosome pixel had "vesicle" underneath it.
    #
    # It happens because the orthogonal slope runs away as the correlation
    # weakens: it tends to (var_B - var_A)/cov, so at R ~ 0.3 the fitted line is
    # near-vertical and the derived cutoff on the second channel is meaningless.
    # The formula is right; it is being asked a question this data cannot answer.
    frac_a = float((A > ta).mean())
    frac_b = float((B > tb).mean())
    info["frac_a"], info["frac_b"] = frac_a, frac_b
    if max(frac_a, frac_b) > COSTES_MAX_SIGNAL_FRAC:
        info["status"] = "not_selective"
        return float(A.min()), float(B.min()), info

    info["status"] = "ok"
    return float(ta), float(tb), info


def chance_manders(B, tb):
    """The Manders M that chance alone would produce: the fraction of the frame
    the reference channel calls positive.

    If the signal channel's pattern were placed with no regard to where the
    reference channel is, the share of its intensity landing on reference-
    positive pixels would just be how much of the frame is reference-positive.
    That is the baseline every M has to be read against, and it is arithmetic,
    not a simulation -- no seed, no iterations, same answer every time.

    It was worth checking rather than assuming. Shifting one channel by a large
    random offset 24 times and taking the median M reproduces this number to two
    or three decimals on all 11 images of the 20260722 set (0.025 vs 0.025 on
    Snap-132, 0.133 vs 0.133 on Snap-138). The randomization was measuring the
    area fraction the long way round.
    """
    B = np.asarray(B)
    if B.size == 0 or not np.isfinite(tb):
        return float("nan")
    return float((B > tb).mean())


def enrichment(M, chance):
    """How many times more overlap than chance. 1.0 is exactly chance.

    This is the whole answer to "is the colocalization above chance", and it is
    an EFFECT SIZE rather than a p-value on purpose. A p-value cannot do this
    job on an image: significance grows with the number of pixels, so a
    megapixel frame makes even a trivial overlap overwhelmingly significant,
    while the question actually being asked -- is the overlap big enough to
    mean anything -- is left unanswered. Enrichment does not move when the
    camera has more pixels.
    """
    if not np.isfinite(M) or not np.isfinite(chance) or chance <= 0:
        return float("nan")
    return float(M / chance)


def costes_significance(A, B, n=None, block=None, seed=0):
    """The randomization test from the same paper, which is the part that says
    whether a colocalization number is above chance.

    One channel is scrambled in blocks the size of a diffraction spot and the
    Pearson r recomputed, many times. The answer is the percentage of scrambles
    the real image beats. Costes' own cutoff is 95%: below that, the overlap you
    measured is what randomly arranged pixels of the same brightness would give.

    Returns the percentage, or nan if the test could not be run.
    """
    n = COSTES_SIGNIFICANCE_N if n is None else n
    block = COSTES_SIGNIFICANCE_BLOCK if block is None else block
    if not n:
        return float("nan")
    A = np.asarray(A)
    B = np.asarray(B)
    if A.ndim == 3:                        # test on the projection; scrambling a
        A = A.max(axis=0)                  # volume block-wise is a different test
        B = B.max(axis=0)
    if A.ndim != 2 or A.shape != B.shape or min(A.shape) < 2 * block:
        return float("nan")

    # Compare like with like: the observed r has to come from the same cropped
    # region the scrambles are built from, or the comparison is between two
    # different pictures.
    ny, nx = (A.shape[0] // block) * block, (A.shape[1] // block) * block
    a_c = np.asarray(A[:ny, :nx], dtype=float)
    b_c = np.asarray(B[:ny, :nx], dtype=float)
    flat_a, flat_b = a_c.ravel(), b_c.ravel()
    npix = flat_a.size
    ma, mb = flat_a.mean(), flat_b.mean()
    sa, sb = flat_a.std(), flat_b.std()
    if sa <= 0 or sb <= 0 or npix < 2:
        return float("nan")

    # Scrambling in blocks only REARRANGES B's pixels, so the scrambled channel
    # keeps B's mean and standard deviation exactly. Every r in the loop then
    # differs only in the dot product, and one dot product is the whole
    # calculation -- which is what makes 100 randomizations affordable on a
    # full-size image instead of a minute of waiting per pair.
    denom = npix * sa * sb
    observed = float((flat_a @ flat_b - npix * ma * mb) / denom)
    if not np.isfinite(observed):
        return float("nan")

    nby, nbx = ny // block, nx // block
    blocks = (b_c.reshape(nby, block, nbx, block)
                 .swapaxes(1, 2).reshape(-1, block, block))
    rng = np.random.default_rng(seed)      # fixed seed: the same image must give
    beaten = 0                             # the same answer twice, or it is not
    for _ in range(int(n)):                # a measurement
        scram = (blocks[rng.permutation(blocks.shape[0])]
                 .reshape(nby, nbx, block, block)
                 .swapaxes(1, 2).reshape(-1))
        r = (flat_a @ scram - npix * ma * mb) / denom
        if observed > r:
            beaten += 1
    return 100.0 * beaten / float(n)


def load_all(path):
    """{role: bg-subtracted image}, {role: colour}, and a per-channel info list."""
    arr = load_planes(path)
    n = arr.shape[0]
    px = pixel_size_um(path)
    if px is None:                             # keep the ball a consistent PHYSICAL size
        px = FALLBACK_PIXEL_UM
        _warn_once("px", f"  [pixel size] missing from a file's metadata; using the fallback "
                         f"{FALLBACK_PIXEL_UM} um/px so the rolling-ball radius stays physical.")
    radius_px = ROLLING_BALL_RADIUS_UM / px
    imgs, colors, meta = {}, {}, []
    usable = [c for c in channel_info(path)
              if not c["transmitted"] and c["index"] < n]
    roles, _spare_lines, _spare_chans = resolve_roles(usable)
    for c in usable:
        r = roles[c["index"]]
        if r in imgs:                          # two dyes on one role -> keep both, by DYE identity
            r = f"{r} ({c['fluor'] or c['name'] or c['index']})"
        while r in imgs:
            r = f"{r}#{c['index']}"
        imgs[r] = subtract_bg(arr[c["index"]], radius_px)
        colors[r] = color_for(r, c["em"])
        meta.append(dict(role=r, dye=c["fluor"] or c["name"],
                         emission_nm=c["em"], channel=c["index"],
                         colour=colors[r], pixel_um=round(px, 4)))
    return imgs, colors, meta


def analyze(path):
    """Every pairwise colocalization for one image.
    Returns (records, roles, qc, channel-meta). Record = (sig, ref, M, R)."""
    imgs, _c, meta = load_all(path)
    roles = list(imgs)
    # Threshold the SAME data that gets measured. A z-stack was being
    # flattened to a max projection to pick the masks and thresholds, which
    # were then applied to the full stack: the Otsu fallback produced a 2D
    # mask for a 3D array, so a stack whose Costes fit was degenerate died
    # with "boolean index did not match indexed array", and where it did not
    # die the numbers were wrong - a stack with every voxel of one dye on the
    # other reported M1 = 0.95 instead of 1.00, because the projection made
    # bright voxels stand in for dim ones underneath. Otsu and Costes both
    # work on a volume directly, so there is nothing to flatten.
    masks = {r: otsu_mask(im) for r, im in imgs.items()}
    qc = [f"low_signal:{r}" for r, mk in masks.items() if mk.sum() < QC_MIN_SIGNAL_PIX]
    recs = []
    want = CO.wanted_set(PAIRS_WANTED)
    for a, b in combinations(roles, 2):
        if want and not COMPUTE_ALL_PAIRS and CO._roles_of(f"{a} in {b}") not in want:
            continue
        A, B = imgs[a], imgs[b]
        # Which threshold was used, and what it was, travels WITH the numbers.
        # The whole "your M is 0.42 and ours is 0.58" argument is settled by one
        # fact -- the cutoff was 3200 here and 130 there -- and that fact used to
        # be computed, used, and thrown away, leaving the two numbers to be
        # reconciled by guesswork. It is now a column.
        used, note = THRESH_METHOD, ""
        ta = tb = np.nan
        if THRESH_METHOD == "costes":
            try:
                ta, tb, cinfo = costes_thresholds(A, B)
                mA, mB = A > ta, B > tb
                if cinfo["status"] != "ok" or mA.sum() == 0 or mB.sum() == 0:
                    used = "otsu"               # Costes did not apply here
                    note = cinfo["status"] if cinfo["status"] != "ok" else "empty_mask"
                    mA, mB = masks[a], masks[b]
                    ta = float(threshold_otsu(A)) if A.max() > A.min() else np.nan
                    tb = float(threshold_otsu(B)) if B.max() > B.min() else np.nan
            except Exception as e:
                used, note = "otsu", f"costes_failed:{type(e).__name__}"
                mA, mB = masks[a], masks[b]
                ta = tb = np.nan
        else:
            mA, mB = masks[a], masks[b]
            ta = float(threshold_otsu(A)) if A.max() > A.min() else np.nan
            tb = float(threshold_otsu(B)) if B.max() > B.min() else np.nan
        both = mA & mB
        M_ab = float(A[both].sum() / A[mA].sum()) if A[mA].sum() > 0 else np.nan
        M_ba = float(B[both].sum() / B[mB].sum()) if B[mB].sum() > 0 else np.nan
        try:                                    # standard Pearson PCC, over the WHOLE image
            R = float(pearsonr(A.ravel(), B.ravel())[0])
        except Exception:
            R = np.nan
        try:
            sig = costes_significance(A, B)
        except Exception:
            sig = np.nan

        # The same image measured by the OTHER threshold rule. This is the
        # single most useful column in the file, and the reason is worth
        # stating: M is a fraction of signal, and "signal" is whatever the
        # cutoff says it is. On a strongly colocalized image Costes and Otsu
        # land in nearly the same place and M barely moves. On a weakly
        # correlated one they do not, and M can be anything -- on Snap-138
        # (R = 0.34) the same pixels give M = 0.90 with the vesicle cutoff at
        # the image median and M = 0.04 with it at the 99.9th percentile.
        # Reporting one number from one rule hides that completely. Reporting
        # both makes "the threshold is deciding this, not the biology" visible
        # without anyone having to reverse-engineer it from a spreadsheet.
        M_alt_ab = M_alt_ba = np.nan
        try:
            if used == "costes":
                aA, aB = masks[a], masks[b]     # the Otsu view of the same image
            else:
                _ta2, _tb2, _ci2 = costes_thresholds(A, B)
                aA, aB = (A > _ta2, B > _tb2) if _ci2["status"] == "ok" else (None, None)
            if aA is not None:
                ab = aA & aB
                if A[aA].sum() > 0:
                    M_alt_ab = float(A[ab].sum() / A[aA].sum())
                if B[aB].sum() > 0:
                    M_alt_ba = float(B[ab].sum() / B[aB].sum())
        except Exception:
            pass

        # What chance alone would have given, and how far above it we landed.
        chance_ab = chance_manders(B, tb)      # "a in b": b is the reference
        chance_ba = chance_manders(A, ta)
        recs.append((a, b, M_ab, M_ba, R,
                     dict(thresh_used=used, thresh_note=note,
                          thresh_a=ta, thresh_b=tb, costes_p=sig,
                          m_alt_ab=M_alt_ab, m_alt_ba=M_alt_ba,
                          chance_ab=chance_ab, chance_ba=chance_ba,
                          enrich_ab=enrichment(M_ab, chance_ab),
                          enrich_ba=enrichment(M_ba, chance_ba))))
    return recs, roles, (";".join(qc) if qc else "ok"), meta


def survey_dyes(root):
    """Which dyes each folder has, by reading one image from EACH of them.

    Folders in one dataset do not have to be stained the same way. Elise's
    had nine folders carrying Cy3 alone and six carrying three dyes, so
    looking at the first folder and stopping - which is what the window did -
    answered "this dataset has 1 dye" about a dataset with three.

    Returns (per_folder, usable, skipped) where per_folder maps a folder to
    the roles found in it, usable are the folders with two or more, and
    skipped are the rest.
    """
    conds = find_conditions(root)
    per_folder, usable, skipped = {}, [], []
    for name, files in conds.items():
        roles = []
        for f in files[:2]:                 # a second try if the first fails
            try:
                chans = [c for c in channel_info(f) if not c.get("transmitted")]
                got, _sl, _sc = resolve_roles(chans)
                roles = list(dict.fromkeys(got.values()))
                if roles:
                    break
            except Exception:
                continue
        per_folder[name] = roles
        (usable if len(roles) >= 2 else skipped).append(name)
    return per_folder, usable, skipped


def find_conditions(root):
    """Every folder in the tree that directly holds .czi, .tif, or .nd2 files is one condition."""
    root = os.path.abspath(root)
    conds = {}
    for dirpath, _dirs, files in os.walk(root):
        rel = os.path.relpath(dirpath, root)
        if OUTPUT_DIRNAME in rel.split(os.sep):
            continue
        imgs = sorted(os.path.join(dirpath, f) for f in files if f.lower().endswith((".czi", ".tif", ".tiff", ".nd2")))
        if imgs:
            label = os.path.basename(root) if rel == "." else rel.replace(os.sep, " / ")
            conds[label] = imgs
    return dict(sorted(conds.items()))


def _safe(s):
    return "".join(c if (c.isalnum() or c in " -_.") else "-" for c in s).strip() or "x"


def check_dye_names(path):
    """Say what each channel will be called, and why.

    Silence here is what made this confusing: a line that matched nothing left
    the channel under the microscope's own name, the run said nothing, and the
    mismatch only surfaced in the folder names at the end.
    """
    try:
        chans = [c for c in channel_info(path) if not c.get("transmitted")]
    except Exception:
        return []
    if not chans:
        return []

    report = []
    roles, spare_lines, spare_chans = resolve_roles(chans, report=report)

    for kind, chan_name, dye, role in report:
        if kind == "only one left":
            print(f"  [dyes] '{chan_name}' -> {role}")
            print(f"  [dyes]     Your line '{dye} = {role}' matched no channel "
                  f"by name, but")
            print(f"  [dyes]     every other line found its channel and this "
                  f"was the only one left,")
            print(f"  [dyes]     so it is the one you meant. Check that is "
                  f"right - '{chan_name}' and")
            print(f"  [dyes]     '{dye}' are not the same word.")
        else:
            print(f"  [dyes] '{chan_name}' read as '{dye}' -> {role}")

    if spare_lines or spare_chans:
        print("  [dyes] ------------------------------------------------------")
        if spare_lines:
            print("  [dyes] These lines matched no channel:")
            for d, r in spare_lines:
                print(f"  [dyes]     {d} = {r}")
        if spare_chans:
            print("  [dyes] These channels were not named by any line, so they")
            print("  [dyes] keep the name your microscope recorded:")
            for c in spare_chans:
                print(f"  [dyes]     {_chan_name(c)}")
        print("  [dyes] Too many left over to work out which is which. Put the")
        print("  [dyes] recorded name on the LEFT of the '=' to settle it, e.g.")
        if spare_chans and spare_lines:
            print(f"  [dyes]     {_chan_name(spare_chans[0])} = "
                  f"{spare_lines[0][1]}")
        print("  [dyes] ------------------------------------------------------")

    print("  [dyes] Results will use: "
          + ", ".join(sorted({str(r) for r in roles.values()})))
    return spare_lines


def print_channel_sheet(path):
    """Single-file mode: show channels, dyes, wavelengths and assigned roles."""
    try:
        chans = channel_info(path)
    except Exception as e:
        print(f"  could not read channel metadata: {e}")
        return
    print(f"  Channel reference sheet  ({os.path.basename(path)})")
    for c in chans:
        role = "transmitted light -> skip" if c["transmitted"] else f"role -> {role_of(c)}"
        em = f"Em {c['em']:.0f}nm" if c["em"] else "-"
        print(f"     channel {c['index']} : {(c['fluor'] or c['name'] or '?'):<32} {em:<10} {role}")


def build_reference(folder):
    """--dyes: list every dye found in a folder, with a plain description."""
    folder = os.path.abspath(folder)
    files = sorted([f for ext in ("*.czi", "*.tif", "*.tiff", "*.nd2") for f in glob.glob(os.path.join(folder, "**", ext), recursive=True)])
    if not files:
        print(f"No .czi, .tif, or .nd2 files found under: {folder}")
        return
    found = {}
    for f in files:
        try:
            chans = channel_info(f)
        except Exception:
            continue
        for c in chans:
            key = c["fluor"] or c["name"] or "?"
            d = found.setdefault(key, dict(em=c["em"], transmitted=c["transmitted"],
                                           channels=set(), files=0))
            d["channels"].add(c["index"]); d["files"] += 1
            if c["em"]:
                d["em"] = c["em"]
    lines = [f"DYE REFERENCE SHEET   ({len(files)} file(s) under {folder})",
             "=" * 72,
             "Copy a dye NAME below into the DYES block, e.g.   Cy3 = vesicle",
             "",
             "THESE NAMES COME FROM THE FILE, NOT FROM THE SAMPLE.",
             "A microscope records whichever dye preset was selected at",
             "acquisition. If that preset was left over from another",
             "experiment, the name here is that preset - not what was",
             "actually stained. You are the one who knows which it was.",
             "",
             "The description under each name is what that NAME usually",
             "refers to. Where it disagrees with what you pipetted, you are",
             "right: put the recorded name on the left and your own name on",
             "the right, e.g.   MitoTracker Deep Red 633 = Lysosome",
             ""]
    for name, d in sorted(found.items(), key=lambda kv: (kv[1]["transmitted"], -(kv[1]["em"] or 0))):
        desc = describe_dye(name)
        what = ("transmitted light (NOT a dye) -> ignored" if d["transmitted"]
                else f"this NAME usually means: {desc[0]}   |   colour: {desc[1]}"
                if desc else "fluorescent dye (name not in the catalogue)")
        em = f"Em {d['em']:.0f} nm" if d["em"] else "no wavelength"
        chs = ", ".join(str(i) for i in sorted(d["channels"]))
        lines += [f"  - {name}", f"        {what}",
                  f"        {em}   |   channel {chs}   |   seen in {d['files']} file(s)", ""]
    text = "\n".join(lines)
    print(text)
    try:
        with open(os.path.join(os.getcwd(), "dye_reference.txt"), "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    except Exception:
        pass


def _rank_channel(chans, n, role):
    """Pick the channel to rank by: the named role, else the first fluorescent one."""
    fl = [c for c in chans if not c["transmitted"] and c["index"] < n]
    if not fl:
        return None
    if role:
        return next((c for c in fl if role_of(c) == role), None)
    return fl[0]


def _stretch(a):
    lo, hi = np.percentile(a, 1), np.percentile(a, 99.5)
    return np.clip((a - lo) / (hi - lo + 1e-9), 0, 1)


def _tint(gray01, colour):
    """Turn a 0..1 grayscale image into an RGB tinted with `colour`."""
    rgb = to_rgb(colour) if (colour and is_color_like(colour)) else (1, 1, 1)
    return np.clip(gray01[..., None] * np.array(rgb), 0, 1)


def _viability_score(circ, cv, solidity, eccentricity, ring_ratio, mean_int, bg, area_um2):
    """Physics-based viability score: 0.0 = definitely live, 1.0 = definitely dead.

    1. AREA / SIZE (weight 4.0) — The most fundamental marker. Dead cells condense
       into tiny masses. Live cells flatten and spread wide.
    2. INTENSITY (weight 3.0) — High Local Intensity = Dead. Lower/Diffuse = Live.
    3. CIRCULARITY (weight 1.5) — Dead cells round up. Live spread irregularly.
    4. INTENSITY UNIFORMITY / CV (weight 1.0) — Granular (Live) vs Uniform (Dead).
    5. HOLLOW RING (weight 1.0) — Late apoptosis pushes vesicles to the edge.
    6. SOLIDITY (weight 2.0) — Cells with "arms" or extensions have low solidity (Live).
    """
    s2b = mean_int / max(bg, 1e-6)

    # ABSOLUTE VETOS (prevents massive blunders)
    if area_um2 > 400:
        return 0.0  # Massive spreading cell -> absolutely live
    if solidity < 0.70 and area_um2 > 30:
        return 0.0  # Cell with massive clear extensions/arms -> absolutely live
    if area_um2 < 30 and s2b > 5.0:
        return 1.0  # Tiny and intensely bright -> absolutely dead

    score = 0.0
    total = 0.0

    # 1. Area: Cellular spreading vs condensation
    w = 4.0
    if area_um2 < 20:
        score += w * 1.0
    elif area_um2 < 120:
        score += w * (1.0 - (area_um2 - 20) / 100)
    total += w

    # 2. Intensity: High local intensity = dead
    w = 3.0
    if s2b > 6.0:
        score += w * 1.0
    elif s2b > 2.0:
        score += w * ((s2b - 2.0) / 4.0)
    total += w

    # 3. Circularity: Spherical collapse
    w = 1.5
    if circ > 0.85:
        score += w * 1.0
    elif circ > 0.65:
        score += w * ((circ - 0.65) / 0.20)
    total += w

    # 4. CV: Uniform vs Granular
    w = 1.0
    if cv < 0.25:
        score += w * 1.0
    elif cv < 0.50:
        score += w * (1.0 - (cv - 0.25) / 0.25)
    total += w

    # 5. Hollow ring
    w = 1.0
    if ring_ratio > 1.8:
        score += w * 1.0
    elif ring_ratio > 1.3:
        score += w * ((ring_ratio - 1.3) / 0.5)
    total += w

    # 6. Solidity: Cells with 'arms' / extensions
    w = 2.0
    if solidity < 0.85:
        score -= w * 1.5  # Heavy negative score ensures odd shapes are live
    elif solidity > 0.92:
        score += w * 0.5  # Solid blob characteristic of condensation/death
    total += w

    return max(0.0, round(score / total, 3))


# NOTE: find_strongest() lived here. It thresholded the DYE channel and kept
# the brightest blobs, which meant it preferentially circled dead cells (they
# pool dye) and sub-cellular patches. Replaced by cell_viability/: brightfield
# segmentation -> learned live-cell picker -> whole-cell uptake -> rank per
# folder. See cell_viability/rank_top.py and "HOW TO USE.txt".


def _style_sheet(ws, title, ncols, nrows, qc_col=None):
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    hr = 3
    ws.cell(1, 1, title).font = Font(bold=True, size=13)
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=ncols)
    head, warn = PatternFill("solid", fgColor="DCE6F1"), PatternFill("solid", fgColor="FCE4D6")
    thin = Side(style="thin", color="AAAAAA")
    for c in range(1, ncols + 1):
        cell = ws.cell(hr, c)
        cell.font = Font(bold=True); cell.fill = head
        cell.alignment = Alignment(horizontal="center"); cell.border = Border(bottom=thin)
    for c in range(1, ncols + 1):
        vals = [ws.cell(r, c).value for r in range(hr, hr + nrows + 1)]
        w = max((len(str(v)) for v in vals if v is not None), default=10)
        ws.column_dimensions[get_column_letter(c)].width = min(42, max(11, w + 2))
    ws.freeze_panes = ws.cell(hr + 1, 1)
    if qc_col:
        for r in range(hr + 1, hr + 1 + nrows):
            if str(ws.cell(r, qc_col).value) not in ("ok", "None", ""):
                for c in range(1, ncols + 1):
                    ws.cell(r, c).fill = warn


def _methods_for_book(root):
    """The same words archive_run writes to disk, for the workbook."""
    try:
        return (f"HOW THIS WAS MEASURED (whole image)\n{'=' * 60}\n"
                f"images        : {root}\n"
                f"thresholding  : {THRESH_METHOD}\n"
                f"background    : {BG_METHOD} "
                f"(rolling ball {ROLLING_BALL_RADIUS_UM} um)\n"
                f"dyes -> roles : {_DYES}\n\n"
                "M (Manders) is the fraction of one dye's signal sitting on\n"
                "pixels where the other dye is above its threshold. It runs 0\n"
                "to 1 and is DIRECTIONAL - 'A in B' and 'B in A' are different\n"
                "questions with different answers, and both are reported.\n\n"
                "R (Pearson) is the correlation of the two channels across the\n"
                "whole frame, -1 to +1. It is sensitive to how much dye there\n"
                "is, not only to where it is. It uses NO threshold, so it is\n"
                "the number to compare against other software first: if R\n"
                "agrees and M does not, the difference is the threshold, and\n"
                "the threshold each image used is in the columns beside M.\n\n"
                "Threshold method / cutoffs: which rule set the line between\n"
                "signal and background, and where it landed. M depends on this\n"
                "completely - the same image thresholded two ways gives two\n"
                "different M - so the cutoff is reported with every number.\n\n"
                "Every value here is ONE PER IMAGE, over the whole frame -\n"
                "cells, background and debris alike. For numbers restricted to\n"
                "the cells that were measured, see Colocalization/In cells.\n")
    except Exception:
        return ""


def _wide_summary(summary, wanted=None):
    """One row per folder, one column block per comparison.

    The long form - condition, pair, mean, std, count - put every folder on
    six rows, left std blank wherever a folder had a single image, and made
    "is Vesicle in Lysosome going down across my doses?" a question you answer
    by reading down a column of thirty-six rows and skipping five out of six.

    Read across a row for one folder; read down a column for one comparison.
    The comparisons asked for in the window come first.
    """
    pairs = list(dict.fromkeys(summary["pair"]))
    if wanted:
        pairs = ([p for p in pairs if CO._roles_of(p) in wanted]
                 + [p for p in pairs if CO._roles_of(p) not in wanted])
    conds = list(dict.fromkeys(summary["condition"]))

    rows = []
    for c in conds:
        row = {"Folder": c}
        for p in pairs:
            hit = summary[(summary["condition"] == c) & (summary["pair"] == p)]
            if len(hit):
                r = hit.iloc[0]
                n = int(r["count"])
                row[f"{p}  M"] = round(float(r["mean"]), 3)
                # a single image has no spread to report; say so rather than
                # leaving a blank that reads like a missing number
                row[f"{p}  SD"] = (round(float(r["std"]), 3)
                                   if n > 1 and r["std"] == r["std"] else "-")
                row[f"{p}  images"] = n
            else:
                row[f"{p}  M"] = "-"
                row[f"{p}  SD"] = "-"
                row[f"{p}  images"] = 0
        rows.append(row)
    return pd.DataFrame(rows), pairs


def save_excel(per_image, summary, path, channels=None, wanted=None,
               stats_text="", methods_text=""):
    """The one workbook for the whole-image run, styled and with units."""
    wide, _pairs = _wide_summary(summary, wanted)
    long = CO.tidy(summary)
    if "M, SD" in long.columns:
        long["M, SD"] = [CO.NO_SD if (n <= 1 or v != v) else round(float(v), 4)
                         for v, n in zip(summary["std"], summary["count"])]
    # "pair" and "meaning" repeat the same words on every row of a sheet that
    # is already one comparison per row, so they say nothing there.
    CO.write_workbook(
        path,
        [("Summary", wide),
         ("Per folder", long),
         ("Per image", CO.tidy(per_image, drop=("meaning",))),
         ("Channels", CO.tidy(channels) if channels is not None else None)],
        notes=[("How this was measured", methods_text or ""),
               ("Statistics", stats_text or ""),
               ("What these mean", CO.README_TEXT)])
    print(f"   Excel report  ->  {os.path.basename(path)}")


def run_stats(df, outdir, wanted=None):
    """Folder against folder, for every comparison.

    This used to pick a control and test everything against it. When no folder
    was named "control" it took whichever came first alphabetically and said
    so in a note - so on a dose series with no folder called control, every
    p-value in the file was against an arbitrary dose, and the note explaining
    that sat above six screens of numbers that looked authoritative.

    Nothing is nominated now. Every pair of folders is compared, Bonferroni
    corrected by how many comparisons were actually run for that metric, which
    is the same thing the cell results do and needs no guess about intent. A
    folder whose name contains "control" is still pointed out, as a place to
    start reading rather than as the only comparison made.
    """
    import itertools

    lines = ["STATISTICS  (colocalization over the whole image)", "=" * 68,
             "Mann-Whitney U, two-sided, between every pair of folders.",
             "p is Bonferroni-corrected by the number of comparisons actually",
             "run for that dye pair. n is IMAGES, not cells - whole-image",
             "colocalization gives one value per image, so n is small and",
             "these are exploratory rather than proof.",
             ""]

    conds = list(dict.fromkeys(df["condition"]))
    named = [c for c in conds if CONTROL_HINT.lower() in str(c).lower()]
    if named:
        lines += [f"Your control appears to be: {named[0]}",
                  "(named for containing '%s' - every folder is still compared"
                  % CONTROL_HINT,
                  " against every other, not only against this one.)", ""]
    else:
        lines += ["No folder is named as a control, so none is assumed.",
                  "Every folder is compared against every other.", ""]

    # A folder with one image gives one number. There is no spread to report
    # and nothing to test it against, and that is a fact about the experiment
    # rather than about the software - so it is said plainly and once, up
    # here, instead of being inferred from "- (single image)" repeated down a
    # column.
    counts = {c: int(df[df["condition"] == c]["file"].nunique()) for c in conds}
    singles = [c for c, n in counts.items() if n < 2]
    if singles:
        lines += ["FOLDERS WITH ONLY ONE IMAGE:"]
        lines += [f"    {c}" for c in singles]
        lines += ["",
                  "Whole-image colocalization gives ONE number per image, so",
                  "these have no standard deviation and cannot be tested "
                  "against",
                  "anything - a spread needs at least two values.",
                  "",
                  "Two ways forward, and they are different questions:",
                  "  * image more fields of those conditions, or",
                  "  * use Colocalization IN CELLS, which measures one value",
                  "    per cell. A single field with twelve cells gives n=12",
                  "    and a real spread, at the cost of the cells in one",
                  "    image not being independent of each other.",
                  ""]

    pairs = sorted(df["pair"].unique())
    if wanted:
        pairs = ([p for p in pairs if CO._roles_of(p) in wanted]
                 + [p for p in pairs if CO._roles_of(p) not in wanted])

    for pair in pairs:
        sub = df[df["pair"] == pair]
        vals = {c: sub[sub["condition"] == c]["M"].dropna().values
                for c in conds}
        have = [c for c in conds if len(vals[c])]
        mark = "  <-- you asked for this one" if (
            wanted and CO._roles_of(pair) in wanted) else ""
        lines.append(f"[{pair}]{mark}")

        if len(have) < 2:
            lines += ["   only one folder has this comparison - nothing to test",
                      ""]
            continue

        groups = [vals[c] for c in have]
        try:
            H, p = kruskal(*groups)
            lines.append(f"   Across all {len(have)} folders "
                         f"(Kruskal-Wallis): H={H:.3f}, p={p:.4f}")
        except Exception as exc:
            lines.append(f"   Across all folders: not testable ({exc})")

        combos = list(itertools.combinations(have, 2))
        testable = [(a, b) for a, b in combos
                    if len(vals[a]) >= 2 and len(vals[b]) >= 2]
        if not testable:
            lines += ["   Every folder has fewer than 2 images here, so no",
                      "   folder-against-folder test is possible.", ""]
            continue

        n_comp = len(testable)
        for a, b in combos:
            na, nb = len(vals[a]), len(vals[b])
            label = (f"   {str(a)[:26]:26s} (n={na}) vs "
                     f"{str(b)[:26]:26s} (n={nb})")
            if na < 2 or nb < 2:
                lines.append(f"{label}   too few images to test")
                continue
            try:
                _u, p = mannwhitneyu(vals[a], vals[b], alternative="two-sided")
            except Exception:
                lines.append(f"{label}   no valid test (no variance)")
                continue
            pc = min(1.0, p * n_comp)
            star = ("***" if pc < 1e-3 else "**" if pc < 1e-2
                    else "*" if pc < 5e-2 else "ns")
            lines.append(f"{label}   p={pc:.3g}  {star}")
        lines.append("")

    text = "\n".join(lines) + "\n"
    _safe_write(os.path.join(outdir, CO.STATS),
                lambda p: open(p, "w", encoding="utf-8").write(text))
    return text


def _w(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
    except Exception:
        pass


def archive_run(outdir, root):
    """Save a copy of this exact script + a run-info file, so the results folder
    documents itself and the run can be reproduced."""
    try:
        shutil.copy(os.path.abspath(__file__),
                    os.path.join(CO.data_dir(outdir), CO.CODE))
    except Exception:
        pass
    try:
        with open(os.path.join(outdir, CO.RUNINFO), "w",
                  encoding="utf-8") as fh:
            fh.write("Colocalization run\n" + "=" * 30 + "\n")
            fh.write(f"when             : {datetime.datetime.now():%Y-%m-%d %H:%M:%S}\n")
            if EXPERIMENT_TITLE:
                fh.write(f"title            : {EXPERIMENT_TITLE}\n")
            fh.write(f"folder           : {root}\n")
            fh.write(f"thresholding     : {THRESH_METHOD}\n")
            if THRESH_METHOD == "costes":
                fh.write("                   Costes automatic threshold: the cutoff slides down the\n")
                fh.write("                   ORTHOGONAL regression line (perpendicular offsets, as in\n")
                fh.write("                   Costes et al. 2004 and Fiji's Coloc_2) by bisection, until\n")
                fh.write("                   the pixels below it stop being positively correlated.\n")
                fh.write("                   The cutoff actually used on each image is a column in\n")
                fh.write(f"                   {CO.PER_IMAGE_CSV} -- it is not left for anyone to guess.\n")
                fh.write("                   Where Costes does not apply (channels with no positive\n")
                fh.write("                   relation, or no crossing to find) the image falls back to\n")
                fh.write("                   Otsu and the 'Threshold method' column says so.\n")
                fh.write("M, other method  : the SAME image measured with the other threshold rule,\n")
                fh.write("                   and the gap between them. This is the column to read\n")
                fh.write("                   first. M is a fraction of 'signal', and 'signal' is\n")
                fh.write("                   whatever the cutoff says it is; when the two rules\n")
                fh.write("                   agree, M is a property of the image, and when they do\n")
                fh.write("                   not, M is a property of the cutoff. A gap over\n")
                fh.write(f"                   {M_SPREAD_WARN:.2f} is flagged 'threshold_decides_M'.\n")
                fh.write("Above chance     : 'Chance M' is the M that chance alone gives -- the\n")
                fh.write("                   fraction of the frame the other dye calls positive.\n")
                fh.write("                   'Enrichment' is M divided by it. 1.0 is exactly chance;\n")
                fh.write(f"                   under {ENRICHMENT_WARN:.1f} is flagged 'barely_above_chance'.\n")
                fh.write("                   This is an effect size, and it is deliberately NOT a\n")
                fh.write("                   p-value. A p-value cannot answer this question on an\n")
                fh.write("                   image: its null distribution narrows as the pixel count\n")
                fh.write("                   grows, so on a megapixel frame even a trivial overlap is\n")
                fh.write("                   overwhelmingly 'significant'. Measured on the 20260722\n")
                fh.write("                   set, the Costes randomization returned 100% for all 11\n")
                fh.write("                   images at every block size from 2 to 64 px, controls\n")
                fh.write("                   included. Enrichment does not move with pixel count.\n")
                if COSTES_SIGNIFICANCE_N:
                    fh.write(f"Costes randomize : ON, {COSTES_SIGNIFICANCE_N} scrambles of "
                             f"{COSTES_SIGNIFICANCE_BLOCK}x{COSTES_SIGNIFICANCE_BLOCK}-pixel blocks.\n")
                    fh.write("                   Only meaningful on small regions; see the note above.\n")
            fh.write(f"background        : {BG_METHOD}  (rolling ball {ROLLING_BALL_RADIUS_UM} um)\n")
            fh.write(f"dyes -> roles    : {_DYES}\n")
            if USE_3D_VOLUMETRIC:
                fh.write(f"3D VOLUMETRIC    : ACTIVE (Z-stacks were NOT flattened)\n")
            fh.write("Manders M        : numerator = intensity where BOTH channels are above\n")
            fh.write("                   threshold; denominator = intensity where the signal channel\n")
            fh.write("                   is above threshold (this project's stated definition).\n")
            fh.write("Pearson R        : standard PCC over the whole image.\n")
            fh.write(f"python           : {platform.python_version()}\n")
            fh.write("(coloc_USED.py in this folder is the exact code that produced these results)\n")
    except Exception:
        pass


def pick_folder():
    try:
        import tkinter as tk
        from tkinter import filedialog
        r = tk.Tk(); r.withdraw()
        d = filedialog.askdirectory(title="Pick the folder with your .czi images")
        r.destroy()
        return d
    except Exception:
        return input("Paste the folder path: ").strip().strip('"')


def run(root, outdir=None):
    conds = find_conditions(root)
    if not conds:
        print(f"No image files found under: {root}")
        return
    # Colocalization/Whole image/, beside the cell results rather than in a
    # folder of its own with a different name from everything else.
    base_out = os.path.abspath(outdir) if outdir else os.path.abspath(root)
    outdir = CO.root(base_out, "whole", create=True)
    datadir = CO.data_dir(outdir)
    _w(os.path.join(CO.root(base_out, create=True), CO.README), CO.README_TEXT)
    try:                                        # fail fast if the folder isn't writable
        _t = os.path.join(outdir, ".writetest")
        open(_t, "w").close(); os.remove(_t)
    except Exception as e:
        print(f"  [error] cannot write results into {outdir}: {e}"); return
    bad = bad_colours()
    if bad:
        print("  [colour] not a recognised colour name (see COLORS.txt); a sensible "
              f"default will be used instead for: {', '.join(bad)}")
    print(f"Found {len(conds)} condition(s) under {root}:")
    for c, fs in conds.items():
        print(f"   {c:<34} {len(fs)} image(s)")
    for fs in conds.values():             # show the dye->role map once (confirm, not configure)
        try:
            print(); print_channel_sheet(fs[0]); print()
            check_dye_names(fs[0])
            break
        except Exception:
            continue

    # Folders without two dyes produce nothing here. That used to happen in
    # silence: nine folders vanished from the results with no line saying
    # why, and the summary simply had fewer rows than the dataset had
    # folders.
    try:
        _per, _usable, _skipped = survey_dyes(root)
        if _skipped:
            print(f"\n  {len(_skipped)} folder(s) have fewer than two dyes and "
                  f"cannot be colocalized:")
            for _n in _skipped:
                _r = _per.get(_n) or []
                print(f"      {str(_n)[:44]:44s} "
                      f"{len(_r)} dye(s): {', '.join(map(str, _r)) or 'none'}")
            print(f"  Measuring the remaining {len(_usable)} folder(s).\n",
                  flush=True)
    except Exception:
        pass

    rows, first_meta = [], None
    
    total_files = sum(len(files) for files in conds.values())
    current_file_idx = 0
    
    for cond, files in conds.items():
        for f in files:
            print(f"__PROGRESS__{current_file_idx},{total_files},measuring images", flush=True)
            current_file_idx += 1
            try:
                recs, _roles, qc, meta = analyze(f)
            except Exception as e:
                print(f"   [skip] {os.path.basename(f)}: {e}")
                continue
            if first_meta is None and meta:
                first_meta = meta
            for a, b, M_ab, M_ba, R, tinfo in recs:
                anti = ";anti_correlated" if (np.isfinite(R) and R <= QC_REVIEW_R_BELOW) else ""
                extra = anti
                if tinfo.get("thresh_note"):
                    extra += ";threshold_" + tinfo["thresh_note"]
                _p = tinfo.get("costes_p", np.nan)
                if np.isfinite(_p) and _p < COSTES_SIGNIFICANCE_MIN:
                    extra += ";not_above_chance"
                _alt = "otsu" if tinfo["thresh_used"] == "costes" else "costes"
                for _pair, _M, _Malt, _ts, _tr, _ch, _en, _mean in (
                        (f"{a} in {b}", M_ab, tinfo["m_alt_ab"],
                         tinfo["thresh_a"], tinfo["thresh_b"],
                         tinfo["chance_ab"], tinfo["enrich_ab"], note_for(a, b)),
                        (f"{b} in {a}", M_ba, tinfo["m_alt_ba"],
                         tinfo["thresh_b"], tinfo["thresh_a"],
                         tinfo["chance_ba"], tinfo["enrich_ba"], note_for(b, a))):
                    _spread = (abs(_M - _Malt)
                               if np.isfinite(_M) and np.isfinite(_Malt) else np.nan)
                    _flag = extra
                    if np.isfinite(_spread) and _spread > M_SPREAD_WARN:
                        _flag += ";threshold_decides_M"
                    if np.isfinite(_en) and _en < ENRICHMENT_WARN:
                        _flag += ";barely_above_chance"
                    rows.append(dict(condition=cond, file=os.path.basename(f),
                                     pair=_pair, M=_M, R=R,
                                     chance_M=_ch, enrichment=_en,
                                     thresh_method=tinfo["thresh_used"],
                                     thresh_sig=_ts, thresh_ref=_tr,
                                     M_alt=_Malt, M_alt_method=_alt,
                                     M_spread=_spread, costes_p=_p,
                                     meaning=_mean, qc=qc + _flag))
    if not rows:
        # Figure out WHY and tell the user clearly
        n_fluor = 0
        if first_meta:
            n_fluor = len(first_meta)
        if n_fluor < 2:
            print(f"\nNothing analysed — your images only have {n_fluor} fluorescent channel(s).")
            print("Colocalization needs at least 2 fluorescent channels (dyes) to compare.")
            print("If you only have 1 dye + brightfield, use 'Brightest Live Cells' instead.")
        else:
            print("\nNothing analysed — no images could be processed.")
            print("Check that your Dye names match what the images contain (click 'List dyes').")
        return

    df = pd.DataFrame(rows)
    summ = (df.groupby(["condition", "pair"])["M"]
              .agg(["mean", "std", "count"]).reset_index())
    _safe_write(os.path.join(datadir, CO.PER_IMAGE_CSV),
                lambda p: df.to_csv(p, index=False))
    _safe_write(os.path.join(datadir, CO.SUMMARY_CSV),
                lambda p: summ.to_csv(p, index=False))
    if first_meta:                        # WHERE the observed wavelengths go: this file
        _safe_write(os.path.join(datadir, CO.CHANNELS_CSV),
                    lambda p: pd.DataFrame(first_meta).to_csv(p, index=False))
    _want = CO.wanted_set(PAIRS_WANTED)
    _stats_text = run_stats(df, outdir, wanted=_want) or ""
    if SAVE_EXCEL:
        chdf = pd.DataFrame(first_meta) if first_meta else None
        save_excel(df, summ, os.path.join(outdir, CO.WORKBOOK),
                   channels=chdf, wanted=_want, stats_text=_stats_text,
                   methods_text=_methods_for_book(root))
    archive_run(outdir, root)

    if SAVE_PLOTS:
        for pair in sorted(df["pair"].unique()):
            sub = summ[summ["pair"] == pair]
            if sub.empty:
                continue
            fig, ax = plt.subplots(figsize=(max(6, 1.2 * len(sub)), 4.5))
            x = np.arange(len(sub))
            ax.bar(x, sub["mean"], yerr=sub["std"], capsize=4, color="#2c7fb8", alpha=0.8)
            _shared, _short = CO.shared_prefix(list(sub["condition"]))
            ax.set_xticks(x)
            ax.set_xticklabels([_short.get(c, c) for c in sub["condition"]],
                               rotation=30, ha="right")
            if _shared:
                ax.set_xlabel(_shared)
            ax.set_ylabel(f"Manders M  ({pair})"); ax.set_ylim(0, 1)
            ax.set_title(df[df["pair"] == pair]["meaning"].iloc[0])
            ax.grid(axis="y", alpha=0.25); plt.tight_layout()
            _pd = CO.pair_dir(outdir, pair, wanted=_want)
            plt.savefig(os.path.join(_pd, CO.CHART), dpi=140)
            plt.close()
            # The numbers behind this chart, beside this chart, in the same
            # styled form as the main workbook. Looking one up meant opening
            # the shared data/ folder and filtering thirty-six rows down to
            # the six belonging to the comparison in front of you.
            _one = sub.copy()
            _tidy = CO.tidy(_one)
            if "M, SD" in _tidy.columns:
                _tidy["M, SD"] = [CO.NO_SD if (n <= 1 or v != v)
                                  else round(float(v), 4)
                                  for v, n in zip(_one["std"], _one["count"])]
            CO.write_workbook(
                os.path.join(_pd, CO.PAIR_WORKBOOK),
                [("Per folder", _tidy),
                 ("Per image", CO.tidy(df[df["pair"] == pair],
                                       drop=("pair", "meaning")))],
                notes=[("What this is",
                        f"{pair}\n\n"
                        + str(df[df["pair"] == pair]["meaning"].iloc[0]
                              if len(df[df["pair"] == pair]) else "")
                        + "\n\n" + CO.README_TEXT)])

        used_dirs = {}
        for cond in conds:
            sub = summ[summ["condition"] == cond]
            roles = sorted({r for p in sub["pair"] for r in p.split(" in ")})
            if len(roles) < 2:
                continue
            M = np.full((len(roles), len(roles)), np.nan)
            for _, r in sub.iterrows():
                sig, ref = r["pair"].split(" in ")
                M[roles.index(sig), roles.index(ref)] = r["mean"]
            np.fill_diagonal(M, 1.0)
            fig, ax = plt.subplots(figsize=(1.1 * len(roles) + 2.5, 1.1 * len(roles) + 2.5))
            im = ax.imshow(M, vmin=0, vmax=1, cmap="viridis")
            ax.set_xticks(range(len(roles))); ax.set_xticklabels(roles, rotation=30, ha="right")
            ax.set_yticks(range(len(roles))); ax.set_yticklabels(roles)
            ax.set_xlabel("reference  (sitting inside ...)")
            ax.set_ylabel("signal  (fraction of ...)")
            for i in range(len(roles)):
                for j in range(len(roles)):
                    if np.isfinite(M[i, j]):
                        ax.text(j, i, f"{M[i, j]:.2f}", ha="center", va="center",
                                color="white" if M[i, j] < 0.6 else "black", fontsize=9)
            ax.set_title(f"Manders matrix — {cond}")
            fig.colorbar(im, ax=ax, fraction=0.046)
            base = _safe(cond); name, k = base, 2
            while name in used_dirs and used_dirs[name] != cond:
                name, k = f"{base}-{k}", k + 1
            used_dirs[name] = cond
            cdir = os.path.join(outdir, CO.PER_CONDITION_DIRNAME, name)
            os.makedirs(cdir, exist_ok=True)
            plt.tight_layout(); plt.savefig(os.path.join(cdir, "matrix.png"), dpi=140)
            plt.close()

    n_qc = int((df["qc"] != "ok").sum())
    print(f"\nDone. Results in: {outdir}")
    if n_qc:
        print(f"{n_qc} row(s) flagged for QC — see the 'qc' column in the CSV / Excel.")
    print("\nMean Manders M per condition / pair:")
    print(summ.round(3).to_string(index=False))
    
    # Panels reload every image again, once per comparison, so on a network
    # drive this is by far the slowest stage. It used to print one line and
    # then work in silence with no completion message, which is
    # indistinguishable from a hang - and if it raised, the run ended on that
    # silence with the results already written but nothing saying so.
    try:
        generate_panels(conds, df, outdir)
    except Exception as exc:
        print(f"\n[panels] could not draw the example images: {exc}")
        print("[panels] Everything above was written and is complete.")
    print(f"\nFinished. Results in: {outdir}", flush=True)


def draw_pair_column(ax0, ax1, ax2, path, sig_role, ref_role, heading):
    """The three rows for ONE image: merged, cytofluorogram, overlap.

    Both the montage and the one-image-per-file figures draw this, so a change
    to how a comparison is shown cannot apply to one and not the other.
    Returns M, or None if the image could not be used.
    """
    import matplotlib.pyplot as plt   # noqa: F401
    from scipy.stats import pearsonr

    try:
        imgs, colors, _meta = load_all(path)
    except Exception:
        return None
    if sig_role not in imgs or ref_role not in imgs:
        return None

    A, B = imgs[sig_role], imgs[ref_role]
    _otsu_a = lambda: threshold_otsu(A) if A.max() > A.min() else 0
    _otsu_b = lambda: threshold_otsu(B) if B.max() > B.min() else 0
    thr_label = "Otsu"
    if getattr(sys.modules[__name__], "THRESH_METHOD", "costes") == "costes":
        try:
            ta, tb, _ci = costes_thresholds(A, B)
            if _ci["status"] == "ok":
                thr_label = "Costes"
            else:                      # the figure must show the cutoff actually
                ta, tb = _otsu_a(), _otsu_b()   # used, not the one that failed
                thr_label = "Otsu (Costes: %s)" % _ci["status"]
        except Exception:
            ta, tb = _otsu_a(), _otsu_b()
    else:
        ta, tb = _otsu_a(), _otsu_b()

    col_sig = colors.get(sig_role) or "green"
    col_ref = colors.get(ref_role) or "magenta"

    A_vis = A.max(axis=0) if A.ndim == 3 else A
    B_vis = B.max(axis=0) if B.ndim == 3 else B
    merged = np.clip(_tint(_stretch(A_vis), col_sig)
                     + _tint(_stretch(B_vis), col_ref), 0, 1)
    ax0.imshow(merged)
    ax0.axis("off")
    ax0.set_title(heading, fontsize=9)

    av, bv = A.ravel(), B.ravel()
    ax1.hexbin(bv + 1, av + 1, xscale="log", yscale="log", bins="log",
               gridsize=60, cmap="viridis", mincnt=1)
    try:
        R = pearsonr(av, bv)[0]
    except Exception:
        R = 0.0
    ax1.set_title(f"R = {R:.2f}   (red dashed = {thr_label} thresholds: "
                  f"{ta:.0f}, {tb:.0f})", fontsize=8)
    ax1.set_xlabel(f"{ref_role} intensity (log)")
    ax1.set_ylabel(f"{sig_role} intensity (log)")
    ax1.axhline(ta + 1, color="red", linestyle="--", linewidth=1)
    ax1.axvline(tb + 1, color="red", linestyle="--", linewidth=1)

    both = (A > ta) & (B > tb)
    m_a = A > ta
    M = float(A[both].sum() / A[m_a].sum()) if A[m_a].sum() > 0 else 0.0
    out = np.copy(merged)
    out[both.max(axis=0) if both.ndim == 3 else both] = [1, 1, 1]
    ax2.imshow(out)
    ax2.axis("off")
    ax2.set_title(f"White = colocalized pixels (M={M:.2f})", fontsize=10)
    return M


def per_image_figures(conds, df, outdir, pair, wanted=None):
    """One figure per image, full size, in a folder of its own.

    The montage is for comparing folders at a glance; it is the wrong thing
    to put a single field into a talk from, or to look at closely. Each image
    gets the same three rows on its own sheet, named for its folder and file.
    """
    import matplotlib.pyplot as plt

    sig_role, ref_role = pair.split(" in ")
    sub = df[df["pair"] == pair]
    if not len(sub):
        return 0
    by_name = {}
    for cond, files in conds.items():
        for f in files:
            by_name[(cond, os.path.basename(f))] = f

    dest = os.path.join(CO.pair_dir(outdir, pair, wanted=wanted),
                        CO.EACH_IMAGE_DIRNAME)
    os.makedirs(dest, exist_ok=True)
    made = 0
    for cond in conds:
        rows = sub[sub["condition"] == cond].sort_values("file")
        for _i, r in rows.iterrows():
            path = by_name.get((cond, str(r["file"])))
            if not path:
                continue
            fig, axes = plt.subplots(3, 1, figsize=(5.2, 13.5))
            head = f"{cond}\n{r['file']}"
            got = draw_pair_column(axes[0], axes[1], axes[2],
                                   path, sig_role, ref_role, head)
            if got is None:
                plt.close(fig)
                continue
            fig.suptitle(pair, fontsize=11)
            fig.tight_layout(rect=(0, 0, 1, 0.975))
            name = CO._safe(f"{cond} - {os.path.splitext(str(r['file']))[0]}")
            fig.savefig(os.path.join(dest, name + ".png"), dpi=130)
            plt.close(fig)
            made += 1
    return made


def _median_image(df, pair, cond, files):
    """The file whose value sits in the middle of this folder, and a note.

    A single field standing in for a folder has to be typical of it. Taking
    the first filename made that a lottery: on a folder of four fields it is
    a one-in-four chance of showing the median one, and a reader has no way
    to know whether they are looking at a good field or a bad one.
    """
    import numpy as _np
    by_name = {os.path.basename(f): f for f in files}
    sub = df[(df["pair"] == pair) & (df["condition"] == cond)]
    sub = sub[_np.isfinite(sub["M"])]
    if not len(sub):
        return files[0], ""
    med = float(sub["M"].median())
    row = sub.iloc[(sub["M"] - med).abs().argsort().iloc[0]]
    path = by_name.get(str(row["file"]), files[0])
    n = len(sub)
    if n == 1:
        return path, "(the only image)"
    return path, f"(middle of {n} images, M={float(row['M']):.2f})"


def contact_sheet(conds, df, outdir, pair, wanted=None, per_row=6):
    """Every image for one comparison, small, with its own number on it.

    The panel shows one field per folder. That is the right size to look at
    and the wrong size to judge spread: a folder of four fields might have
    three that agree and one that does not, and the panel cannot say which
    kind of folder it is showing.
    """
    import matplotlib.pyplot as plt

    sub = df[df["pair"] == pair]
    sub = sub[[c == c for c in sub["M"]]]
    if not len(sub):
        return None
    by_name = {}
    for cond, files in conds.items():
        for f in files:
            by_name[(cond, os.path.basename(f))] = f

    items = []
    for cond in conds:
        rows = sub[sub["condition"] == cond].sort_values("file")
        for _i, r in rows.iterrows():
            p = by_name.get((cond, str(r["file"])))
            if p:
                items.append((cond, str(r["file"]), p, float(r["M"])))
    if not items:
        return None

    sig_role, ref_role = pair.split(" in ")
    n = len(items)
    cols = min(per_row, n)
    rws = (n + cols - 1) // cols
    # Each caption is two lines, so the rows need room for them. Without it
    # the row above sat on top of the row below's title and the file name -
    # the one thing that identifies the picture - was the part covered up.
    fig, axes = plt.subplots(rws, cols, figsize=(3.0 * cols, 3.9 * rws),
                             squeeze=False)
    med_all = float(sub["M"].median())
    for k, (cond, fname, path, M) in enumerate(items):
        ax = axes[k // cols][k % cols]
        ax.axis("off")
        try:
            imgs, colors, _meta = load_all(path)
            if sig_role not in imgs or ref_role not in imgs:
                continue
            A, B = imgs[sig_role], imgs[ref_role]
            A = A.max(axis=0) if A.ndim == 3 else A
            B = B.max(axis=0) if B.ndim == 3 else B
            merged = np.clip(_tint(_stretch(A), colors.get(sig_role) or "green")
                             + _tint(_stretch(B), colors.get(ref_role) or "magenta"),
                             0, 1)
            ax.imshow(merged)
        except Exception:
            continue
        # a field far from the middle is the one worth a second look
        odd = "  <-- unlike the rest" if abs(M - med_all) > 0.25 else ""
        ax.set_title(f"{str(cond)[-28:]}\n{fname}   M={M:.2f}{odd}",
                     fontsize=7.5)
    for k in range(n, rws * cols):
        axes[k // cols][k % cols].axis("off")
    fig.suptitle(f"{pair} - every image ({n}), M under each", fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.96), h_pad=2.6)
    out = os.path.join(CO.pair_dir(outdir, pair, wanted=wanted),
                       CO.CONTACT_SHEET)
    fig.savefig(out, dpi=110)
    plt.close(fig)
    return out


def generate_panels(conds, df, outdir):
    if not getattr(sys.modules[__name__], "SAVE_PANELS", True):
        return
    import matplotlib.pyplot as plt
    from scipy.stats import pearsonr
    
    
    pairs = sorted(df["pair"].unique())
    if not pairs:
        return
    
    _want = CO.wanted_set(PAIRS_WANTED)
    print(f"\nGenerating example images for {len(pairs)} comparison(s) - "
          f"each one reloads your images, so this is the slow part...",
          flush=True)
    for pi, pair in enumerate(pairs, 1):
        print(f"__PROGRESS__{pi - 1},{len(pairs)},drawing example images",
              flush=True)
        print(f"   [{pi}/{len(pairs)}] {pair}", flush=True)
        sig_role, ref_role = pair.split(" in ")
        # Only the folders that HAVE this comparison. Taking every folder drew
        # a column for each one that lacks these two dyes, and those columns
        # came out as empty white axes - on a dataset with folders of
        # different stains that was most of the figure.
        have = set(df.loc[df["pair"] == pair, "condition"])
        cond_list = [c for c in conds if c in have]
        n_conds = len(cond_list)
        if n_conds == 0:
            continue
            
        fig, axes = plt.subplots(3, n_conds, figsize=(4.5 * n_conds, 11), squeeze=False)
        
        for c_idx, cond in enumerate(cond_list):
            fs = conds[cond]
            # The MIDDLE image of the folder for this comparison, not the
            # first one. "First" means whichever filename sorts earliest,
            # which is a property of how the microscope numbers files and
            # nothing to do with the data - so the picture standing for the
            # folder could be its best field or its worst, and there was no
            # way to tell which.
            file_path, rep_note = _median_image(df, pair, cond, fs)
            draw_pair_column(
                axes[0, c_idx], axes[1, c_idx], axes[2, c_idx],
                file_path, sig_role, ref_role,
                f"{cond}\n({os.path.basename(file_path)})  {rep_note}")
            
        plt.tight_layout()
        try:
            plt.savefig(os.path.join(CO.pair_dir(outdir, pair, wanted=_want),
                                     CO.PANEL),
                        dpi=150)
            try:
                contact_sheet(conds, df, outdir, pair, wanted=_want)
            except Exception as _exc:
                print(f"      (could not draw the every-image sheet: {_exc})")
            if getattr(sys.modules[__name__], "SAVE_EACH_IMAGE", False):
                try:
                    _n = per_image_figures(conds, df, outdir, pair,
                                           wanted=_want)
                    if _n:
                        print(f"      {_n} single-image figure(s)", flush=True)
                except Exception as _exc:
                    print(f"      (could not draw single images: {_exc})")
        except Exception as e:
            print(f"   [save] panel: {e}")
        plt.close(fig)


# ===========================================================================
#  Publication-Ready Fluorescence Output
# ===========================================================================

def _make_publication_charts(top_live, top_dead, outdir, role_col):
    """Generate publication-ready bar charts with dot plots and representative panels.
    Style modeled on Figure 5 from reference papers: bars with individual data points."""
    if not top_live:
        print("   [charts] No live cells to chart.")
        return

    chartdir = os.path.join(outdir, "charts")
    os.makedirs(chartdir, exist_ok=True)

    df = pd.DataFrame(top_live)
    conditions = list(dict.fromkeys(df["condition"]))  # preserve discovery order
    n_conds = len(conditions)

    # ── 1. Main Bar Chart with Dot Plot (like Figure 5b/d) ──────────────
    print("   Generating publication bar chart...", flush=True)
    means = [df[df["condition"] == c]["corrected_intensity"].mean() for c in conditions]
    stds = [df[df["condition"] == c]["corrected_intensity"].std() for c in conditions]

    fig, ax = plt.subplots(figsize=(max(4.5, 1.6 * n_conds), 5.5))
    x = np.arange(n_conds)

    # Bars: neutral grey fill with black edge, error bars
    ax.bar(x, means, yerr=stds, capsize=5, color="#d9d9d9", edgecolor="black",
           linewidth=0.8, width=0.55, error_kw=dict(lw=1.2, capthick=1.0))

    # Individual dots (jittered strip plot)
    rng = np.random.default_rng(42)
    for i, cond in enumerate(conditions):
        vals = df[df["condition"] == cond]["corrected_intensity"].values
        jitter = rng.uniform(-0.12, 0.12, len(vals))
        ax.scatter(x[i] + jitter, vals, color="black", s=18, zorder=5, alpha=0.65,
                   edgecolors="none")

    ax.set_xticks(x)
    ax.set_xticklabels(conditions, rotation=30, ha="right", fontsize=10)
    ax.set_ylabel("Intensity (a.u.)", fontsize=12)
    ax.set_ylim(bottom=0)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.tick_params(axis="both", which="both", direction="out")

    _t = (EXPERIMENT_TITLE + "\n") if EXPERIMENT_TITLE else ""
    ax.set_title(f"{_t}Corrected Fluorescence Intensity", fontsize=13, fontweight="bold")

    plt.tight_layout()
    _safe_write(os.path.join(chartdir, "fluorescence_bar_chart.png"),
                lambda p: (plt.savefig(p, dpi=200, bbox_inches="tight"), plt.close()))
    plt.close("all")
    print("   Bar chart  ->  charts/fluorescence_bar_chart.png")

    # ── 2. Per-Image Bar Chart ──────────────────────────────────────────
    print("   Generating per-image chart...", flush=True)
    img_groups = df.groupby(["condition", "file"], sort=False)["corrected_intensity"]
    img_means = img_groups.mean()
    img_stds = img_groups.std().fillna(0)
    img_counts = img_groups.count()

    labels = [f"{os.path.splitext(f)[0]}" for (c, f) in img_means.index]
    cond_labels = [c for (c, f) in img_means.index]
    n_imgs = len(labels)

    if n_imgs > 0:
        fig, ax = plt.subplots(figsize=(max(6, 0.8 * n_imgs), 5.5))
        x_img = np.arange(n_imgs)

        # Colour bars by condition
        cond_colors = {}
        palette = ["#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f",
                    "#edc948", "#b07aa1", "#ff9da7", "#9c755f", "#bab0ac"]
        for i, c in enumerate(conditions):
            cond_colors[c] = palette[i % len(palette)]
        bar_colors = [cond_colors[c] for c in cond_labels]

        ax.bar(x_img, img_means.values, yerr=img_stds.values, capsize=3,
               color=bar_colors, edgecolor="black", linewidth=0.5, width=0.7,
               error_kw=dict(lw=0.8))

        # Overlay individual dots per image
        for idx, ((cond, fname), grp) in enumerate(df.groupby(["condition", "file"], sort=False)):
            vals = grp["corrected_intensity"].values
            jitter = rng.uniform(-0.15, 0.15, len(vals))
            ax.scatter(idx + jitter, vals, color="black", s=12, zorder=5, alpha=0.5,
                       edgecolors="none")

        # Add cell count annotation on each bar
        for idx, cnt in enumerate(img_counts.values):
            ax.text(idx, img_means.values[idx] + img_stds.values[idx] + 0.5,
                    f"n={cnt}", ha="center", va="bottom", fontsize=6, color="grey")

        ax.set_xticks(x_img)
        ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=7)
        ax.set_ylabel("Corrected Intensity (a.u.)", fontsize=11)
        ax.set_ylim(bottom=0)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

        # Legend for conditions
        from matplotlib.patches import Patch
        legend_patches = [Patch(facecolor=cond_colors[c], edgecolor="black", label=c)
                          for c in conditions]
        ax.legend(handles=legend_patches, loc="upper right", fontsize=8,
                  framealpha=0.8, edgecolor="grey")

        _t = (EXPERIMENT_TITLE + " — ") if EXPERIMENT_TITLE else ""
        ax.set_title(f"{_t}Per-Image Fluorescence", fontsize=12, fontweight="bold")
        plt.tight_layout()
        _safe_write(os.path.join(chartdir, "fluorescence_per_image.png"),
                    lambda p: (plt.savefig(p, dpi=200, bbox_inches="tight"), plt.close()))
        plt.close("all")
        print("   Per-image chart  ->  charts/fluorescence_per_image.png")

    # ── 3. Representative Panel (one crop per condition, like Fig 5a/c) ─
    print("   Generating representative panel...", flush=True)
    rep_cells = []
    for cond in conditions:
        cond_cells = [r for r in top_live if r["condition"] == cond and "raw_crop_all" in r]
        if cond_cells:
            rep_cells.append(cond_cells[0])  # rank #1 = brightest

    if rep_cells:
        n_rep = len(rep_cells)
        fig, axes = plt.subplots(1, n_rep, figsize=(2.8 * n_rep, 3.2), squeeze=False)
        for k, r in enumerate(rep_cells):
            ax = axes[0, k]
            try:
                chans = channel_info(r["path"])
                c_idx = next(i for i, ch in enumerate(chans) if role_of(ch) == r["role"])
                cr = r["raw_crop_all"][c_idx]
                cr_vis = cr.max(axis=0) if cr.ndim == 3 else cr
                r_col = _COLORS.get(r["role"]) or role_col or "white"
                ax.imshow(_tint(_stretch(cr_vis), r_col))
            except Exception:
                ax.text(0.5, 0.5, "N/A", transform=ax.transAxes, ha="center")
            ax.axis("off")
            ax.set_title(f"{r['condition']}\nS/B={r['signal_to_bg']}", fontsize=9)

        _t = (EXPERIMENT_TITLE + " — ") if EXPERIMENT_TITLE else ""
        fig.suptitle(f"{_t}Representative Cells (Rank #1 per condition)",
                     fontsize=11, fontweight="bold")
        plt.tight_layout()
        _safe_write(os.path.join(chartdir, "representative_panel.png"),
                    lambda p: (plt.savefig(p, dpi=180, bbox_inches="tight"), plt.close()))
        plt.close("all")
        print("   Panel  ->  charts/representative_panel.png")


def _save_fluorescence_excel(top, top_live, top_dead, cols, outdir):
    """Save comprehensive master Excel with Summary, All Cells, Per Image,
    Per Condition, and Statistics sheets."""
    try:
        import openpyxl  # noqa: F401
    except ImportError:
        print("   (openpyxl not installed -> skipping master Excel; CSVs still written)")
        return

    path = os.path.join(outdir, "fluorescence_summary_report.xlsx")
    print("   Writing master Excel report...", flush=True)

    try:
        with pd.ExcelWriter(path, engine="openpyxl") as xl:
            # ── Sheet 1: Summary (Per Condition) ────────────────────────
            if top_live:
                df_live = pd.DataFrame(top_live)
                summary_rows = []
                conditions = list(dict.fromkeys(r["condition"] for r in top_live))
                for cond in conditions:
                    g = df_live[df_live["condition"] == cond]
                    ci = g["corrected_intensity"]
                    mi = g["mean_intensity"]
                    summary_rows.append(dict(
                        condition=cond,
                        n_cells=len(g),
                        n_images=g["file"].nunique(),
                        mean_corrected_intensity=round(ci.mean(), 2),
                        sd_corrected_intensity=round(ci.std(), 2),
                        sem_corrected_intensity=round(ci.std() / max(1, len(g) ** 0.5), 2),
                        mean_raw_intensity=round(mi.mean(), 2),
                        mean_background=round(g["background"].mean(), 2),
                        mean_signal_to_bg=round(g["signal_to_bg"].mean(), 2),
                    ))
                df_summary = pd.DataFrame(summary_rows)
                df_summary.to_excel(xl, sheet_name="Summary", startrow=2, index=False)
                _style_sheet(xl.book["Summary"],
                             "Fluorescence Summary — Mean ± SD per Condition (Live Cells)",
                             df_summary.shape[1], df_summary.shape[0])

            # ── Sheet 2: All Cells ──────────────────────────────────────
            df_all = pd.DataFrame(top)[cols].round(3)
            df_all.to_excel(xl, sheet_name="All Cells", startrow=2, index=False)
            qc_col = (list(df_all.columns).index("flag") + 1) if "flag" in df_all.columns else None
            _style_sheet(xl.book["All Cells"], "All Quantified Cells (Live + Dead)",
                         df_all.shape[1], df_all.shape[0], qc_col)

            # ── Sheet 3: Per Image ──────────────────────────────────────
            if top_live:
                img_rows = []
                for (cond, fname), grp in df_live.groupby(["condition", "file"], sort=False):
                    ci = grp["corrected_intensity"]
                    img_rows.append(dict(
                        condition=cond,
                        image=fname,
                        n_cells=len(grp),
                        n_live=int((grp["status"] == "live").sum()),
                        mean_corrected_intensity=round(ci.mean(), 2),
                        sd_corrected_intensity=round(ci.std(), 2) if len(grp) > 1 else 0,
                        mean_raw_intensity=round(grp["mean_intensity"].mean(), 2),
                        mean_background=round(grp["background"].mean(), 2),
                        mean_signal_to_bg=round(grp["signal_to_bg"].mean(), 2),
                    ))
                df_img = pd.DataFrame(img_rows)
                df_img.to_excel(xl, sheet_name="Per Image", startrow=2, index=False)
                _style_sheet(xl.book["Per Image"],
                             "Per-Image Breakdown — Cell Counts & Mean Intensity",
                             df_img.shape[1], df_img.shape[0])

            # ── Sheet 4: Live Only ──────────────────────────────────────
            if top_live:
                df_live_out = pd.DataFrame(top_live)[cols].round(3)
                df_live_out.to_excel(xl, sheet_name="Live Only", startrow=2, index=False)
                _style_sheet(xl.book["Live Only"], "Live Cells Only",
                             df_live_out.shape[1], df_live_out.shape[0])

            # ── Sheet 5: Dead/Flagged ───────────────────────────────────
            if top_dead:
                df_dead_out = pd.DataFrame(top_dead)[cols].round(3)
                df_dead_out.to_excel(xl, sheet_name="Dead or Flagged", startrow=2, index=False)
                qc_col_d = (list(df_dead_out.columns).index("flag") + 1) if "flag" in df_dead_out.columns else None
                _style_sheet(xl.book["Dead or Flagged"], "Dead or Flagged Cells",
                             df_dead_out.shape[1], df_dead_out.shape[0], qc_col_d)

            # ── Sheet 6: Statistics ─────────────────────────────────────
            if top_live and len(conditions) >= 2:
                stat_rows = _fluorescence_stats(df_live, conditions)
                if stat_rows:
                    df_stats = pd.DataFrame(stat_rows)
                    df_stats.to_excel(xl, sheet_name="Statistics", startrow=2, index=False)
                    _style_sheet(xl.book["Statistics"],
                                 "Statistical Tests (Kruskal-Wallis + Mann-Whitney, Bonferroni-corrected)",
                                 df_stats.shape[1], df_stats.shape[0])

        print(f"   Master Excel  ->  {os.path.basename(path)}")
    except PermissionError:
        print(f"   [save] {os.path.basename(path)} is open — close it and re-run; skipped.")
    except Exception as e:
        print(f"   [save] could not write master Excel: {e}")


def _fluorescence_stats(df_live, conditions):
    """Kruskal-Wallis + pairwise Mann-Whitney with Bonferroni correction.
    Returns a list of dicts suitable for a DataFrame / Excel sheet."""
    from scipy.stats import kruskal, mannwhitneyu

    rows = []

    # Identify control condition
    hint = [c for c in conditions if CONTROL_HINT.lower() in c.lower()]
    ctrl = hint[0] if hint else conditions[0]

    groups = {c: df_live[df_live["condition"] == c]["corrected_intensity"].dropna().values
              for c in conditions}
    valid_groups = [g for g in groups.values() if len(g) >= 2]

    # Kruskal-Wallis across all conditions
    if len(valid_groups) >= 2:
        try:
            H, p_kw = kruskal(*valid_groups)
            rows.append(dict(test="Kruskal-Wallis (across all)",
                             comparison="All conditions",
                             statistic=round(H, 4), p_value=round(p_kw, 6),
                             corrected_p="—",
                             n_control="—", n_treatment="—",
                             note=f"Control assumed: {ctrl}"))
        except Exception as e:
            rows.append(dict(test="Kruskal-Wallis", comparison="All conditions",
                             statistic="N/A", p_value="N/A", corrected_p="N/A",
                             n_control="—", n_treatment="—", note=str(e)))

    # Pairwise Mann-Whitney vs control
    others = [c for c in conditions if c != ctrl]
    n_comp = max(1, sum(1 for c in others if len(groups.get(c, [])) >= 1))

    for c in others:
        cv = groups.get(ctrl, np.array([]))
        gv = groups.get(c, np.array([]))
        if len(cv) < 1 or len(gv) < 1:
            rows.append(dict(test="Mann-Whitney U",
                             comparison=f"{ctrl} vs {c}",
                             statistic="N/A", p_value="N/A", corrected_p="N/A",
                             n_control=len(cv), n_treatment=len(gv),
                             note="Insufficient data"))
            continue
        try:
            U, p_mw = mannwhitneyu(cv, gv, alternative="two-sided")
            p_corr = min(1.0, p_mw * n_comp)
            sig = "***" if p_corr < 0.001 else "**" if p_corr < 0.01 else "*" if p_corr < 0.05 else "ns"
            rows.append(dict(test="Mann-Whitney U",
                             comparison=f"{ctrl} vs {c}",
                             statistic=round(U, 2), p_value=round(p_mw, 6),
                             corrected_p=round(p_corr, 6),
                             n_control=len(cv), n_treatment=len(gv),
                             note=f"{sig} (Bonferroni, {n_comp} comparisons)"))
        except Exception as e:
            rows.append(dict(test="Mann-Whitney U",
                             comparison=f"{ctrl} vs {c}",
                             statistic="N/A", p_value="N/A", corrected_p="N/A",
                             n_control=len(cv), n_treatment=len(gv),
                             note=str(e)))
    return rows


def _save_per_image_excel(top_live, outdir):
    """Save a per-image breakdown Excel with dot-plot-ready data."""
    if not top_live:
        return
    try:
        import openpyxl  # noqa: F401
    except ImportError:
        return

    path = os.path.join(outdir, "fluorescence_per_image_report.xlsx")
    print("   Writing per-image Excel report...", flush=True)

    try:
        df = pd.DataFrame(top_live)
        with pd.ExcelWriter(path, engine="openpyxl") as xl:
            # ── Sheet 1: Image Summary ──────────────────────────────────
            img_rows = []
            for (cond, fname), grp in df.groupby(["condition", "file"], sort=False):
                ci = grp["corrected_intensity"]
                img_rows.append(dict(
                    condition=cond,
                    image=fname,
                    n_cells=len(grp),
                    mean_corrected_intensity=round(ci.mean(), 2),
                    sd=round(ci.std(), 2) if len(grp) > 1 else 0,
                    min_intensity=round(ci.min(), 2),
                    max_intensity=round(ci.max(), 2),
                    mean_signal_to_bg=round(grp["signal_to_bg"].mean(), 2),
                    mean_area_um2=round(grp["area_um2"].mean(), 2),
                ))
            df_img = pd.DataFrame(img_rows)
            df_img.to_excel(xl, sheet_name="Image Summary", startrow=2, index=False)
            _style_sheet(xl.book["Image Summary"],
                         "Per-Image Summary — One Row per Image",
                         df_img.shape[1], df_img.shape[0])

            # ── Sheet 2: Dot Data (for copy-paste into GraphPad/Prism) ──
            # Columns: condition, image, corrected_intensity
            # This is the raw data behind the dot plots
            dot_cols = ["condition", "file", "corrected_intensity", "mean_intensity",
                        "background", "signal_to_bg", "area_um2"]
            df_dot = df[dot_cols].copy()
            df_dot = df_dot.rename(columns={"file": "image"})
            df_dot = df_dot.round(3)
            df_dot.to_excel(xl, sheet_name="Dot Data", startrow=2, index=False)
            _style_sheet(xl.book["Dot Data"],
                         "Dot Plot Data — Each Row = One Cell (for GraphPad / Prism)",
                         df_dot.shape[1], df_dot.shape[0])

            # ── Sheet 3: Pivoted for Prism (conditions as columns) ──────
            # Each condition is a column, each row is one cell.
            # Prism and GraphPad prefer this layout for grouped analyses.
            conditions = list(dict.fromkeys(df["condition"]))
            max_n = max(len(df[df["condition"] == c]) for c in conditions)
            pivot_data = {}
            for c in conditions:
                vals = df[df["condition"] == c]["corrected_intensity"].values
                padded = list(vals) + [None] * (max_n - len(vals))
                pivot_data[c] = padded
            df_pivot = pd.DataFrame(pivot_data)
            df_pivot.to_excel(xl, sheet_name="Prism Format", startrow=2, index=False)
            _style_sheet(xl.book["Prism Format"],
                         "Prism-Ready Format — Conditions as Columns (copy directly into Prism)",
                         df_pivot.shape[1], df_pivot.shape[0])

        print(f"   Per-image Excel  ->  {os.path.basename(path)}")
    except PermissionError:
        print(f"   [save] {os.path.basename(path)} is open — close it and re-run; skipped.")
    except Exception as e:
        print(f"   [save] could not write per-image Excel: {e}")


def main():
    global USE_3D_VOLUMETRIC
    args = sys.argv[1:]
    
    if "--3d" in args:
        USE_3D_VOLUMETRIC = True
        args.remove("--3d")

    if args and args[0] in ("--dyes", "--reference", "--sheet"):
        folder = args[1] if len(args) > 1 else FOLDER
        if not folder:
            print('Usage:  python coloc.py --dyes "path\\to\\folder"'); return
        build_reference(folder); return
    if args and os.path.isfile(args[0]) and args[0].lower().endswith((".czi", ".tif", ".tiff", ".nd2")):
        print_channel_sheet(args[0]); return
    root = (args[0] if args else None) or FOLDER or pick_folder()
    if not root:
        print("No folder given."); return
    if not os.path.isdir(root):
        print(f"Folder not found: {root}"); return
    run(root)


# Rchin was here :)   (please keep this credit)
__author__ = "Rchin Bari"

if __name__ == "__main__":
    main()
