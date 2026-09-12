"""
livecell.py - core engine for the live-cell selection + ranking pipeline.
=========================================================================
This module fixes the fundamental error of the original detector, which
thresholded the DYE channel and therefore circled bright blobs (dead cells
pool dye, so they scored highest) and sub-cellular bright patches.

Everything here works the other way round:

  1. SEGMENT whole cells from BRIGHTFIELD ONLY (Cellpose-SAM). Cell identity
     must never depend on how much dye the cell took up.
  2. DESCRIBE each candidate with morphology / refractility / texture /
     ISOLATION features - again brightfield-only, so the later selection is
     independent of the readout being measured.
  3. MEASURE, per whole cell, the integrated and mean Cy3 above a local
     background ring. Not the brightest patch in the image, and not the
     brightest patch inside the cell - the whole cell.

Selection (which cells are "ones I would circle") is learned from human
annotations in pick_model.py. Ranking per folder happens in rank_top.py.

Segmentation is expensive (~13 s/image on CPU), so masks are cached under
<dataset>/viability_workspace/segcache/ keyed by a hash of the segmentation
parameters; changing a parameter invalidates the cache automatically.

Build the cache for a dataset:
    python -m cell_viability.livecell "<dataset folder>" [--force]
"""
import os
import sys
import json
import hashlib
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import coloc  # noqa: E402

WORKSPACE_DIRNAME = "viability_workspace"
SEGCACHE_DIRNAME = "segcache"

# Where the app keeps its own working files: the segmentation cache, the
# picker it trains, your hand corrections, your live/dead scores.
#
# NOT in the folder of images. That folder is very often a share on a
# microscope server, read by other people, backed up on someone else's
# schedule and not ours to write into - choosing it as the SOURCE of images
# is not permission to leave a folder in it. It is not the results folder
# either: results get zipped and sent to a colleague, and a segmentation
# cache has no business travelling with them.
#
# So it lives with the application, keyed by which dataset it belongs to.
# That also means the corrections and the trained picker follow the DATASET,
# and are still there when the same images are analysed into a new results
# folder tomorrow.
APP_WORKSPACES = os.path.join(os.path.expanduser("~"), ".riji", "workspaces")


def _dataset_slug(root):
    """A readable, unique folder name for one dataset path."""
    import re
    full = os.path.abspath(root).rstrip("\\/")
    base = re.sub(r'[<>:"/\\|?*]', "-", os.path.basename(full)) or "dataset"
    digest = hashlib.md5(full.lower().encode("utf-8")).hexdigest()[:8]
    return f"{base[:48]}-{digest}"


def workspace(root, create=True, verbose=True):
    """The working folder for this dataset, outside the dataset.

    Anything already sitting in an older in-dataset workspace is brought
    across the first time, so trained pickers, hand corrections and the
    segmentation cache are not silently abandoned. The originals are left
    alone: deleting a folder from someone's server is their call, not ours.
    """
    root = os.path.abspath(root)
    target = os.path.join(APP_WORKSPACES, _dataset_slug(root))
    legacy = os.path.join(root, WORKSPACE_DIRNAME)

    if not os.path.isdir(target) and os.path.isdir(legacy):
        import shutil
        try:
            shutil.copytree(legacy, target)
            if verbose:
                print(f"[workspace] moved your saved work out of the images "
                      f"folder:\n             from {legacy}\n"
                      f"             to   {target}\n"
                      f"             The old copy is untouched and can be "
                      f"deleted.", flush=True)
        except Exception as exc:
            if verbose:
                print(f"[workspace] could not copy {legacy}: {exc}", flush=True)

    if create:
        os.makedirs(target, exist_ok=True)
    return target

# Cellpose-SAM settings. Defaults recover ~100% of hand-circled cells at
# IoU>=0.3 on this data (validated in pick_model.diagnose), so we deliberately
# segment PERMISSIVELY here and let the learned picker do the rejecting -
# a cell that is never segmented can never be recovered downstream.
SEG_PARAMS = dict(
    flow_threshold=0.4,
    cellprob_threshold=0.0,
    min_size=15,
)
SEG_VERSION = "cpsam-v3-bf-scaled"     # bump to invalidate every cached mask

# ── Everything below is in MICRONS, never pixels ──────────────────────────
# A microscope at 0.28 um/px renders the same cell over ~5x the pixel area as
# one at 0.62 um/px. Any parameter fixed in pixels therefore means a different
# physical thing on every microscope, and a picker trained on one dataset does
# not transfer to another. Pixel counts are derived per image from that image's
# own pixel size, so the analysis is identical in physical terms everywhere.

# Cellpose is run at this pixel size. Images are rescaled to it beforehand and
# the resulting masks scaled back, so cells always present to the network at a
# consistent apparent size. Without this, high-magnification images fail
# outright: Cellpose found 1 object in a field of large, healthy cells.
ANALYSIS_PIXEL_UM = 0.60

# Illumination flattening: these images have a strong corner-to-corner gradient,
# so raw intensity features would encode WHERE a cell sits rather than what it
# looks like. All intensity features are computed on the flattened image.
FLATTEN_SIGMA_UM = 25.0

NEIGHBOUR_MARGIN_UM = 50.0             # window for isolation features
RIM_BAND_UM = 1.9                      # boundary band width
CORE_ERODE_UM = 3.7                    # how far in the "core" starts
OUTER_BAND_UM = 2.5                    # halo band just outside the cell
RING_OUTER_UM = 18.7                   # background annulus, outer edge
RING_INNER_UM = 3.7                    # background annulus, inner edge
MIN_AREA_UM2 = 25.0                    # below this it is debris, not a cell
MAX_AREA_UM2 = 6000.0                  # above this it is a merged clump


def _iters(um, px_um, lo=1):
    """Pixel iterations for a physical distance, at least `lo`."""
    return max(lo, int(round(um / max(px_um, 1e-6))))


# ──────────────────────────────────────────────────────────────────────────
#  Image loading
# ──────────────────────────────────────────────────────────────────────────

def safe_name(s):
    return "".join(c if c.isalnum() or c in "-_ ." else "_" for c in str(s))


def load_channels(path):
    """Return (brightfield_2d, dye_2d_or_None, pixel_size_um).

    Z-stacks are collapsed with a maximum-intensity projection. The dye
    channel is whatever non-transmitted channel exists; datasets with no
    fluorescence (the brightfield-only control) return None for it.
    """
    arr = coloc.load_planes(path)
    chans = coloc.channel_info(path)
    bf_c = next((c for c in chans if c["transmitted"] and c["index"] < arr.shape[0]), None)
    dye_c = next((c for c in chans if not c["transmitted"] and c["index"] < arr.shape[0]), None)
    if bf_c is None:
        raise ValueError("no brightfield channel")
    bf = arr[bf_c["index"]]
    bf = bf.max(axis=0) if bf.ndim == 3 else bf
    dye = None
    if dye_c is not None:
        d = arr[dye_c["index"]]
        dye = (d.max(axis=0) if d.ndim == 3 else d).astype(np.float64)
    px = coloc.pixel_size_um(path) or coloc.FALLBACK_PIXEL_UM
    return bf.astype(np.float64), dye, float(px)


def stretch(a, lo_p=1.0, hi_p=99.5):
    lo, hi = np.percentile(a, lo_p), np.percentile(a, hi_p)
    return np.clip((a - lo) / max(1e-9, hi - lo), 0.0, 1.0)


def polygon_mask(poly, shape):
    """Rasterise a hand-drawn polygon [(x, y), ...] into a boolean mask."""
    from PIL import Image as PImage, ImageDraw as PDraw
    im = PImage.new("L", (shape[1], shape[0]), 0)
    if len(poly) >= 3:
        PDraw.Draw(im).polygon([(float(x), float(y)) for x, y in poly], fill=1)
    return np.array(im, dtype=bool)


def flatten_illumination(bf_norm, px_um, sigma_um=FLATTEN_SIGMA_UM):
    """Subtract a heavily blurred copy: removes the field-illumination gradient
    so intensity features describe the CELL, not its position in the frame.
    The blur radius is physical, so the same structure is removed on every
    microscope."""
    from scipy.ndimage import gaussian_filter
    return bf_norm - gaussian_filter(bf_norm, max(1.0, sigma_um / max(px_um, 1e-6)))


# ──────────────────────────────────────────────────────────────────────────
#  Segmentation (+ disk cache)
# ──────────────────────────────────────────────────────────────────────────

_CP_MODEL = None


def _get_model():
    global _CP_MODEL
    if _CP_MODEL is None:
        import logging
        logging.getLogger("cellpose").setLevel(logging.ERROR)
        from cellpose import models
        gpu = False
        try:
            import torch
            gpu = bool(torch.cuda.is_available())
        except Exception:
            gpu = False
        _CP_MODEL = models.CellposeModel(gpu=gpu)
    return _CP_MODEL


def _seg_key():
    payload = json.dumps(dict(SEG_PARAMS, version=SEG_VERSION), sort_keys=True)
    return hashlib.md5(payload.encode()).hexdigest()[:10]


def segment_brightfield(bf, px_um=None):
    """Cellpose-SAM instance segmentation on the brightfield channel.

    The image is first rescaled to ANALYSIS_PIXEL_UM so cells present at a
    consistent apparent size regardless of objective or camera, then the labels
    are scaled back to the original resolution. Returns int32 labels
    (0 = background) at the ORIGINAL image size.
    """
    from skimage.transform import resize
    model = _get_model()
    img = stretch(bf)
    scale = 1.0
    if px_um and px_um > 0:
        scale = px_um / ANALYSIS_PIXEL_UM
    if abs(scale - 1.0) > 0.10:            # not worth resampling for a few %
        h = max(64, int(round(img.shape[0] * scale)))
        w = max(64, int(round(img.shape[1] * scale)))
        img = resize(img, (h, w), order=1, preserve_range=True,
                     anti_aliasing=(scale < 1.0))
    res = model.eval(img, **SEG_PARAMS)
    masks = res[0].astype(np.int32)
    if masks.shape != bf.shape:
        masks = resize(masks, bf.shape, order=0, preserve_range=True,
                       anti_aliasing=False).astype(np.int32)
    return masks


def cached_masks(src_path, condition, file_name, ws, force=False, verbose=True):
    """Segment once, reuse forever. Returns the int32 label image."""
    cdir = os.path.join(ws, SEGCACHE_DIRNAME)
    os.makedirs(cdir, exist_ok=True)
    cpath = os.path.join(
        cdir, f"{safe_name(condition)}__{safe_name(file_name)}__{_seg_key()}.npz")
    if not force and os.path.isfile(cpath):
        try:
            with np.load(cpath) as z:
                return z["masks"].astype(np.int32)
        except Exception:
            pass    # corrupt cache entry: fall through and recompute
    bf, _dye, px = load_channels(src_path)
    masks = segment_brightfield(bf, px)
    np.savez_compressed(cpath, masks=masks.astype(np.int16))
    if verbose:
        print(f"  segmented {condition} / {file_name}: {int(masks.max())} objects",
              flush=True)
    return masks


def list_images(root):
    """[{condition, file, path}] for every image, in a stable order.
    'condition' is the containing folder - one experiment per folder."""
    root = os.path.abspath(root)
    conds = coloc.find_conditions(root)
    conds = {k: v for k, v in conds.items() if WORKSPACE_DIRNAME not in k}
    out = []
    for cond in sorted(conds):
        for p in sorted(conds[cond]):
            out.append(dict(condition=cond, file=os.path.basename(p), path=p))
    return out


def build_segcache(root, force=False):
    ws = workspace(root)
    os.makedirs(ws, exist_ok=True)
    items = list_images(root)
    print(f"[segcache] {len(items)} images, key={_seg_key()}", flush=True)
    n_obj = 0
    for i, it in enumerate(items, 1):
        try:
            m = cached_masks(it["path"], it["condition"], it["file"], ws,
                             force=force, verbose=False)
            n_obj += int(m.max())
            print(f"  [{i}/{len(items)}] {it['condition'][:26]:26s} {it['file'][:22]:22s} "
                  f"{int(m.max()):4d} objects", flush=True)
        except Exception as exc:
            print(f"  [{i}/{len(items)}] {it['file']}: FAILED {exc}", flush=True)
    print(f"[segcache] done: {n_obj} objects total -> "
          f"{os.path.join(ws, SEGCACHE_DIRNAME)}", flush=True)


# ──────────────────────────────────────────────────────────────────────────
#  Per-cell features - BRIGHTFIELD ONLY (never the dye)
# ──────────────────────────────────────────────────────────────────────────
#  Grouped to mirror the criteria a human actually applies:
#    shape       - is it spread and healthy, or rounded-up and dying?
#    refractile  - dying cells are phase-bright with a halo
#    texture     - granular/blebbing vs smooth cytoplasm
#    isolation   - is it touching neighbours? (overlapping cells are unusable)
#    framing     - is it cut off by the field of view?

FEATURE_COLS = [
    # shape.  area enters as a log: cell size spans an order of magnitude and
    # the raw value would dominate a linear model. equiv_diam_um (r=0.96 with
    # area) and convex_deficiency (identically 1-solidity) are deliberately
    # excluded - exact/near collinearity makes fitted weights meaningless.
    "log_area", "perimeter_um", "circularity", "solidity",
    "eccentricity", "extent", "aspect_ratio", "perim_area_ratio",
    # refractility / intensity (on the flattened brightfield)
    "int_mean", "int_std", "int_cv", "int_p05", "int_p95", "int_dyn_range",
    "bright_frac", "dark_frac",
    # halo / rim
    "rim_minus_core", "halo_minus_core", "rim_contrast",
    # texture
    "grad_mean", "grad_p90", "lap_std",
    # isolation
    "gap_um", "touch_frac", "n_neighbours", "occupancy",
    # framing
    "edge_dist_um",
]


def cell_features(labels, bf, px_um):
    """One feature dict per segmented object. Returns (rows, keep_ids).

    Objects outside the plausible size range are dropped here - they are
    debris or merged clumps and no downstream stage should see them.
    """
    from skimage.measure import regionprops
    from scipy.ndimage import (distance_transform_edt, binary_erosion,
                               binary_dilation, sobel, laplace)

    bf_n = stretch(bf)
    flat = flatten_illumination(bf_n, px_um)
    gx, gy = sobel(flat, axis=1), sobel(flat, axis=0)
    # per-micron, not per-pixel, so texture is comparable across magnifications
    grad = np.hypot(gx, gy) / max(px_um, 1e-6)
    lap = laplace(flat) / max(px_um ** 2, 1e-9)

    margin = _iters(NEIGHBOUR_MARGIN_UM, px_um, 8)
    it_rim = _iters(RIM_BAND_UM, px_um)
    it_core = _iters(CORE_ERODE_UM, px_um, 2)
    it_outer = _iters(OUTER_BAND_UM, px_um)

    H, W = bf_n.shape
    hi_thr = np.percentile(flat, 97.0)
    lo_thr = np.percentile(flat, 3.0)

    props = regionprops(labels)
    rows, keep = [], []

    for r in props:
        area_um2 = r.area * px_um * px_um
        if area_um2 < MIN_AREA_UM2 or area_um2 > MAX_AREA_UM2:
            continue

        mnr, mnc, mxr, mxc = r.bbox

        # Work inside one padded window rather than the full frame: the same
        # numbers, several times faster, and no interaction with the borders.
        r0 = max(0, mnr - margin)
        c0 = max(0, mnc - margin)
        r1 = min(H, mxr + margin)
        c1 = min(W, mxc + margin)
        lab_w = labels[r0:r1, c0:c1]
        flat_w = flat[r0:r1, c0:c1]
        grad_w = grad[r0:r1, c0:c1]
        lap_w = lap[r0:r1, c0:c1]
        m_full = (lab_w == r.label)

        # ---- shape ----
        perim_px = float(r.perimeter) if r.perimeter else 1.0
        circ = float(np.clip(4 * np.pi * r.area / (perim_px ** 2 + 1e-9), 0, 1.5))
        try:
            solidity = float(r.solidity)
        except Exception:
            solidity = 1.0
        maj = float(getattr(r, "axis_major_length", 0.0) or 1.0)
        mnn = float(getattr(r, "axis_minor_length", 0.0) or 1.0)

        # ---- intensity inside the cell (flattened image) ----
        vals = flat_w[m_full]
        v_mean, v_std = float(vals.mean()), float(vals.std())
        p05, p95 = np.percentile(vals, [5, 95])

        # ---- rim vs core: dying cells are refractile with a bright halo ----
        er3 = binary_erosion(m_full, iterations=it_rim)
        er6 = binary_erosion(m_full, iterations=it_core)
        band = m_full & ~er3
        core = er6 if er6.any() else m_full
        outer = binary_dilation(m_full, iterations=it_outer) & ~m_full
        core_mean = float(flat_w[core].mean()) if core.any() else v_mean
        band_mean = float(flat_w[band].mean()) if band.any() else v_mean
        outer_mean = float(flat_w[outer].mean()) if outer.any() else 0.0
        # Differences, not ratios: the flattened image is centred near zero, so
        # dividing by the core level would amplify noise instead of describing
        # the halo. A dying, refractile cell shows a bright rim and a bright
        # surround relative to its own interior.
        rim_minus_core = band_mean - core_mean
        halo_minus_core = outer_mean - core_mean
        rim_contrast = band_mean - outer_mean

        # ---- isolation: the criterion the old feature set completely lacked ----
        me_w = m_full
        others_w = (lab_w != 0) & ~me_w

        if others_w.any():
            gap_px = float(distance_transform_edt(~others_w)[me_w].min())
            n_neigh = int(len(np.unique(lab_w[others_w])))
        else:
            gap_px = float(margin)
            n_neigh = 0
        occupancy = float(others_w.mean())

        halo1 = binary_dilation(me_w, iterations=1) & ~me_w
        touch_frac = (float((halo1 & others_w).sum()) / max(1, int(halo1.sum()))
                      if halo1.any() else 0.0)

        edge_dist = float(min(mnr, mnc, H - mxr, W - mxc)) * px_um

        rows.append(dict(
            area_um2=area_um2,
            log_area=float(np.log(max(area_um2, 1e-3))),
            equiv_diam_um=float(getattr(r, "equivalent_diameter_area",
                                        0.0) or 0.0) * px_um,
            perimeter_um=perim_px * px_um,
            circularity=circ,
            solidity=solidity,
            eccentricity=float(r.eccentricity),
            extent=float(r.extent),
            aspect_ratio=maj / mnn,
            perim_area_ratio=perim_px / (r.area ** 0.5 + 1e-9),
            int_mean=v_mean,
            int_std=v_std,
            int_cv=v_std / (abs(v_mean) + 0.02),
            int_p05=float(p05),
            int_p95=float(p95),
            int_dyn_range=float(p95 - p05),
            bright_frac=float((vals > hi_thr).mean()),
            dark_frac=float((vals < lo_thr).mean()),
            rim_minus_core=rim_minus_core,
            halo_minus_core=halo_minus_core,
            rim_contrast=rim_contrast,
            grad_mean=float(grad_w[m_full].mean()),
            grad_p90=float(np.percentile(grad_w[m_full], 90)),
            lap_std=float(lap_w[m_full].std()),
            gap_um=gap_px * px_um,
            touch_frac=touch_frac,
            n_neighbours=float(n_neigh),
            occupancy=occupancy,
            edge_dist_um=edge_dist,
            # provenance (not features)
            _label_id=int(r.label),
            _x=float(r.centroid[1]),
            _y=float(r.centroid[0]),
            _bbox=(int(mnr), int(mnc), int(mxr), int(mxc)),
        ))
        keep.append(int(r.label))

    return rows, keep


# ──────────────────────────────────────────────────────────────────────────
#  Whole-cell dye measurement
# ──────────────────────────────────────────────────────────────────────────

def measure_uptake(mask, dye, px_um):
    """Whole-cell Cy3 above a LOCAL background ring.

    Deliberately measures the entire cell footprint: the question is which
    CELL is brightest overall, not which patch of pixels is brightest. The
    ring background handles uneven illumination without a global assumption.
    """
    from scipy.ndimage import binary_dilation
    n_px = int(mask.sum())
    if n_px == 0:
        return dict(cy3_integrated=0.0, cy3_mean=0.0,
                    cy3_raw_mean=0.0, cy3_background=0.0, n_pixels=0)
    if dye is None:
        return dict(cy3_integrated=0.0, cy3_mean=0.0,
                    cy3_raw_mean=0.0, cy3_background=0.0, n_pixels=n_px)

    # Dilating on a local window rather than the whole frame: identical result,
    # but the cost no longer scales with image size for every single cell.
    ys, xs = np.where(mask)
    pad = _iters(RING_OUTER_UM, px_um, 3) + 4
    H, W = mask.shape
    r0, c0 = max(0, ys.min() - pad), max(0, xs.min() - pad)
    r1, c1 = min(H, ys.max() + pad + 1), min(W, xs.max() + pad + 1)
    m_w = mask[r0:r1, c0:c1]
    dye_w = dye[r0:r1, c0:c1]

    ring = (binary_dilation(m_w, iterations=_iters(RING_OUTER_UM, px_um, 3))
            & ~binary_dilation(m_w, iterations=_iters(RING_INNER_UM, px_um)))
    bg = float(np.median(dye_w[ring])) if ring.sum() >= 50 else float(np.median(dye))
    vals = dye_w[m_w].astype(np.float64) - bg
    vals[vals < 0] = 0.0
    return dict(
        cy3_integrated=float(vals.sum()),
        cy3_mean=float(vals.mean()),
        cy3_raw_mean=float(dye[mask].mean()),
        cy3_raw_integrated=float(dye[mask].sum()),
        cy3_background=bg,
        n_pixels=n_px,
    )


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print('Usage: python -m cell_viability.livecell "<dataset folder>" [--force]')
        sys.exit(1)
    build_segcache(args[0], force="--force" in sys.argv)


def package_file(name):
    """A data file shipped alongside the code, found however the app is run.

    The trained pickers and the dead-cell model are .json files that live
    beside these modules. A PyInstaller build keeps the .py modules inside an
    archive, so __file__ points at a path that does not exist on disk, while
    the data files are unpacked next to the bundle. Resolving by __file__
    alone therefore found nothing in the packaged Windows app, and the very
    first button failed with "No cell picker available" on a machine that had
    every model it needed.

    Looks beside this file first (running from source), then in the unpacked
    bundle. Returns the source-relative path when neither exists, so the
    caller's own "missing model" message is what the user sees.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    cands = [os.path.join(here, name)]
    base = getattr(sys, "_MEIPASS", None)
    if base:
        cands.append(os.path.join(base, "cell_viability", name))
        cands.append(os.path.join(base, name))
    for p in cands:
        if os.path.isfile(p):
            return p
    return cands[0]
