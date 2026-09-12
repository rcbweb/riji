"""
pick_model.py - learn "which cells would the human circle?"
============================================================
The human drew polygons around the cells they consider usable: alive, spread,
isolated, whole. Cellpose segments far more objects than that (~60 per image
vs ~7 circled). This module learns the difference.

How the training set is built
-----------------------------
For every image the human ENGAGED with (drew at least one polygon on), every
segmented object is matched to the polygons by IoU:

  * matched   -> POSITIVE ("a cell I would circle")
  * unmatched -> NEGATIVE ("seen and passed over")

Images that were opened but left empty are NOT used: skipping a whole image
usually means "this folder does not matter", not "every object here is bad".

Unmatched objects are noisy negatives - the human may simply have stopped
early on an image. Each image therefore carries a confidence weight based on
how thoroughly it was annotated, so densely-worked images dominate.

Honest evaluation
-----------------
Cross-validation is GROUPED BY IMAGE. Cells from one image never appear in
both train and test, so the reported numbers are not inflated by the model
memorising an image's illumination or cell population.

Selection uses BRIGHTFIELD FEATURES ONLY - never the dye. If dye brightness
influenced which cells were selected, then ranking those cells by brightness
would be circular and the result meaningless.

Run:
    python -m cell_viability.pick_model "<dataset>" --build   # make training set
    python -m cell_viability.pick_model "<dataset>"           # train + report
"""
import os
import sys
import json
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cell_viability import livecell as LC  # noqa: E402

WORKSPACE_DIRNAME = LC.WORKSPACE_DIRNAME
TRAIN_CSV = "pick_training.csv"
MODEL_JSON = "pick_model.json"

MATCH_IOU = 0.35            # polygon <-> mask overlap that counts as the same cell
MATCH_CONTAINMENT = 0.70    # or: mask almost entirely inside the polygon

# Operating point.
#
# The threshold is set to retain a target fraction of the cells the human
# actually circled, NOT to hit a precision target. Reason: the positives are
# clean labels, but the "negatives" are only *unlabelled* - an object the human
# never circled may be a genuinely bad cell OR a perfectly good cell they simply
# did not get to. Measured precision is therefore a LOWER BOUND and optimising
# it drives the threshold to a degenerate corner (100% precision, 2% recall).
# Recall against the human's own picks is the one number that is trustworthy.
TARGET_RECALL = 0.80

# Features that describe how ALONE a cell is. In a sparse culture these are
# decisive: a cell touching a neighbour has an ambiguous boundary, so the human
# skips it. In a CONFLUENT monolayer every cell touches its neighbours, so
# demanding isolation would reject the entire population and keep only the few
# cells at the edge of the field - an unrepresentative sample. A second model
# is therefore trained without them, and chosen automatically for confluent
# datasets. See choose_model_for().
ISOLATION_FEATURES = ["gap_um", "touch_frac", "n_neighbours", "occupancy"]

# A dataset counts as confluent when the typical cell is in contact with its
# neighbours around much of its perimeter.
CONFLUENT_TOUCH_FRAC = 0.25

# Below this many examples of either class, a dataset-specific picker would be
# worse than the shipped one.
MIN_TRAIN_PER_CLASS = 15


class NotEnoughLabels(Exception):
    """Raised instead of fitting a picker to too few examples."""


# ──────────────────────────────────────────────────────────────────────────
#  Training-set construction
# ──────────────────────────────────────────────────────────────────────────

def build_training_set(root):
    import pandas as pd

    root = os.path.abspath(root)
    ws = LC.workspace(root)
    ann_dir = os.path.join(ws, "annotations")

    rows = []
    n_poly_total = n_matched = n_lost_to_filter = 0
    per_image = []

    # A dataset can be taught two ways: by drawing example cells in
    # annotate_app, or purely by correcting the automatic picks in review_app.
    # Neither is required, so a missing annotations folder is normal.
    for jn in (sorted(os.listdir(ann_dir)) if os.path.isdir(ann_dir) else []):
        if not jn.endswith(".json"):
            continue
        with open(os.path.join(ann_dir, jn), encoding="utf-8") as fh:
            d = json.load(fh)
        polys = d.get("polygons", [])
        if not polys:
            continue                      # opened but empty: no usable evidence
        src = d.get("src_path", "")
        if not os.path.isfile(src):
            print(f"  [skip] missing source for {d['file']}")
            continue

        try:
            bf, _dye, px = LC.load_channels(src)
            masks = LC.cached_masks(src, d["condition"], d["file"], ws, verbose=False)
        except Exception as exc:
            print(f"  [skip] {d['file']}: {exc}")
            continue

        feats, keep_ids = LC.cell_features(masks, bf, px)
        if not feats:
            continue

        # rasterise the human polygons once
        gts = [LC.polygon_mask(p, bf.shape) for p in polys]

        # IoU / containment matrix over surviving objects only
        obj_masks = {f["_label_id"]: (masks == f["_label_id"]) for f in feats}
        pairs = []
        for gi, g in enumerate(gts):
            g_area = int(g.sum())
            if g_area == 0:
                continue
            for f in feats:
                lid = f["_label_id"]
                mm = obj_masks[lid]
                inter = int((g & mm).sum())
                if inter == 0:
                    continue
                union = int((g | mm).sum())
                iou = inter / union
                cont = inter / max(1, int(mm.sum()))
                if iou >= MATCH_IOU or cont >= MATCH_CONTAINMENT:
                    pairs.append((iou, gi, lid))

        # greedy one-to-one assignment, best overlap first
        pairs.sort(reverse=True)
        used_g, used_l, match_of = set(), set(), {}
        for iou, gi, lid in pairs:
            if gi in used_g or lid in used_l:
                continue
            used_g.add(gi)
            used_l.add(lid)
            match_of[lid] = iou

        n_poly_total += len(polys)
        n_matched += len(used_g)
        n_lost_to_filter += len(polys) - len(used_g)

        n_pos_img = len(used_g)
        for f in feats:
            lid = f["_label_id"]
            is_pos = lid in match_of
            rows.append(dict(
                condition=d["condition"], file=d["file"], src_path=src,
                label_id=lid, x=round(f["_x"], 1), y=round(f["_y"], 1),
                is_pick=int(is_pos), match_iou=round(match_of.get(lid, 0.0), 3),
                n_poly_img=len(polys),
                **{k: f[k] for k in LC.FEATURE_COLS},
            ))

        per_image.append((d["condition"], d["file"], len(polys), n_pos_img, len(feats)))
        print(f"  {d['condition'][:26]:26s} {d['file'][:20]:20s} "
              f"polys={len(polys):3d} matched={n_pos_img:3d} objects={len(feats):3d}",
              flush=True)

    # ---- corrections made in review_app are the strongest labels there are ----
    # An object the human never circled is only *unlabelled*. An object they
    # explicitly kept or rejected while looking at it is a definite judgement,
    # so it is added with a higher weight and overrides any earlier guess.
    n_corr = 0
    from cell_viability import rank_top as RT
    cur = RT.load_curation(ws)
    junk_all = cur.get("discard", {})
    if cur["overrides"] or cur["manual"] or junk_all:
        by_img = {}
        for it in LC.list_images(root):
            by_img[RT.curation_key(it["condition"], it["file"])] = it
        seen = {(r["condition"], r["file"], r["label_id"]) for r in rows}

        touched = list(cur["overrides"]) + list(cur["manual"]) + list(junk_all)
        for ckey in list(dict.fromkeys(touched)):
            o = cur["overrides"].get(ckey, {})
            it = by_img.get(ckey)
            if it is None:
                continue
            incl = set(int(i) for i in o.get("include", []))
            excl = set(int(i) for i in o.get("exclude", []))
            polys = cur["manual"].get(ckey, [])
            # "Not a cell" is the strongest negative there is: not an object
            # the human merely passed over, but one they said should never be
            # picked. For the picker that trains exactly like a rejection.
            junk = set(int(i) for i in junk_all.get(ckey, []))
            if not (incl or excl or polys or junk):
                continue
            try:
                bf, _dye, px = LC.load_channels(it["path"])
                masks = LC.cached_masks(it["path"], it["condition"], it["file"],
                                        ws, verbose=False)
            except Exception:
                continue
            manual_ids = set()
            if polys:
                masks = masks.copy()
                nxt = int(masks.max()) + 1
                for poly in polys:
                    mm = LC.polygon_mask([(float(x), float(y)) for x, y in poly],
                                         masks.shape)
                    if mm.sum() >= 6:
                        masks[mm] = nxt
                        manual_ids.add(nxt)
                        nxt += 1
            feats, _k = LC.cell_features(masks, bf, px)
            for f in feats:
                lid = int(f["_label_id"])
                if lid in manual_ids or lid in incl:
                    lab = 1
                elif lid in excl or lid in junk:
                    lab = 0
                else:
                    continue
                trip = (it["condition"], it["file"], lid)
                if trip in seen:
                    for r in rows:            # a correction beats a guess
                        if (r["condition"], r["file"], r["label_id"]) == trip:
                            r["is_pick"] = lab
                            r["from_correction"] = 1
                            break
                else:
                    rows.append(dict(
                        condition=it["condition"], file=it["file"],
                        src_path=it["path"], label_id=lid,
                        x=round(f["_x"], 1), y=round(f["_y"], 1),
                        is_pick=lab, match_iou=0.0, n_poly_img=99,
                        from_correction=1,
                        **{k: f[k] for k in LC.FEATURE_COLS}))
                n_corr += 1

    if not rows:
        raise SystemExit(
            "Nothing to learn from yet.\n"
            "Either draw example cells (annotate_app), or correct some cells "
            "in the review window, then try again.")

    df = pd.DataFrame(rows)
    if "from_correction" not in df.columns:
        df["from_correction"] = 0
    df["from_correction"] = df["from_correction"].fillna(0).astype(int)

    # Confidence in an image's NEGATIVES scales with how thoroughly it was worked.
    df["w"] = np.where(df["is_pick"] == 1, 1.0,
                       np.clip(df["n_poly_img"] / 5.0, 0.30, 1.0))
    df.loc[df["from_correction"] == 1, "w"] = 2.0

    out = os.path.join(ws, TRAIN_CSV)
    df.to_csv(out, index=False)

    print(f"\n[build] images used      : {len(per_image)}")
    print(f"[build] human polygons   : {n_poly_total}")
    print(f"[build] matched to a mask: {n_matched} "
          f"({100*n_matched/max(1,n_poly_total):.1f}% segmentation recall)")
    if n_lost_to_filter:
        print(f"[build] UNMATCHED polys  : {n_lost_to_filter}  "
              f"(segmentation missed these, or size filter removed them)")
    if n_corr:
        print(f"[build] your corrections : {n_corr} cell(s) from review_app, "
              f"weighted x2 (an explicit judgement beats an unlabelled object)")
    print(f"[build] training rows    : {len(df)}  "
          f"({int((df['is_pick']==1).sum())} positive / "
          f"{int((df['is_pick']==0).sum())} negative)")
    print(f"[build] -> {out}")
    return out


# ──────────────────────────────────────────────────────────────────────────
#  Models (pure NumPy - no scikit-learn, so the PyInstaller build survives)
# ──────────────────────────────────────────────────────────────────────────

def _sigmoid(z):
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))


class LogisticModel:
    """L2-regularised logistic regression with sample weights."""

    kind = "logistic"

    def __init__(self, l2=2.0, iters=3000, lr=0.5):
        self.l2, self.iters, self.lr = l2, iters, lr

    def fit(self, X, y, w):
        self.mean_ = X.mean(axis=0)
        self.std_ = X.std(axis=0) + 1e-9
        Z = (X - self.mean_) / self.std_
        n, d = Z.shape
        self.w_ = np.zeros(d)
        self.b_ = 0.0
        wn = w / w.mean()
        for _ in range(self.iters):
            p = _sigmoid(Z @ self.w_ + self.b_)
            g = wn * (p - y)
            self.w_ -= self.lr * (Z.T @ g / n + self.l2 * self.w_ / n)
            self.b_ -= self.lr * g.mean()
        return self

    def predict_proba(self, X):
        Z = (X - self.mean_) / self.std_
        return _sigmoid(Z @ self.w_ + self.b_)

    def to_dict(self):
        return dict(kind="logistic", mean=self.mean_.tolist(),
                    std=self.std_.tolist(), weights=self.w_.tolist(),
                    bias=float(self.b_))


class GBMModel:
    """Gradient-boosted shallow trees, logistic loss, Newton leaf values.

    Captures the interactions a linear model cannot - e.g. "round" is fine for
    a small healthy cell but damning when combined with a bright halo.
    """

    kind = "gbm"

    def __init__(self, n_trees=160, lr=0.07, max_depth=2, min_leaf=10, l2=1.0):
        self.n_trees, self.lr = n_trees, lr
        self.max_depth, self.min_leaf, self.l2 = max_depth, min_leaf, l2

    def _build(self, X, g, h, idx, depth):
        G, H = g[idx].sum(), h[idx].sum()
        leaf = -G / (H + self.l2)
        if depth >= self.max_depth or len(idx) < 2 * self.min_leaf:
            return dict(leaf=float(leaf))

        best = None
        parent = G * G / (H + self.l2)
        for j in range(X.shape[1]):
            v = X[idx, j]
            order = np.argsort(v, kind="mergesort")
            vs, gs, hs = v[order], g[idx][order], h[idx][order]
            cg, ch = np.cumsum(gs), np.cumsum(hs)
            # only split where the value actually changes
            valid = np.zeros(len(vs), dtype=bool)
            valid[:-1] = vs[:-1] < vs[1:]
            lo, hi = self.min_leaf - 1, len(vs) - self.min_leaf
            if hi <= lo:
                continue
            valid[:lo] = False
            valid[hi:] = False
            if not valid.any():
                continue
            GL, HL = cg[valid], ch[valid]
            GR, HR = G - GL, H - HL
            gain = (GL * GL / (HL + self.l2) + GR * GR / (HR + self.l2) - parent)
            k = int(np.argmax(gain))
            if best is None or gain[k] > best[0]:
                pos = np.flatnonzero(valid)[k]
                thr = 0.5 * (vs[pos] + vs[pos + 1])
                best = (float(gain[k]), j, float(thr))

        if best is None or best[0] <= 1e-9:
            return dict(leaf=float(leaf))

        _gain, j, thr = best
        left = idx[X[idx, j] <= thr]
        right = idx[X[idx, j] > thr]
        if len(left) < self.min_leaf or len(right) < self.min_leaf:
            return dict(leaf=float(leaf))
        return dict(f=int(j), t=float(thr),
                    l=self._build(X, g, h, left, depth + 1),
                    r=self._build(X, g, h, right, depth + 1))

    @staticmethod
    def _apply(tree, X):
        out = np.zeros(len(X))
        stack = [(tree, np.arange(len(X)))]
        while stack:
            node, idx = stack.pop()
            if len(idx) == 0:
                continue
            if "leaf" in node:
                out[idx] = node["leaf"]
                continue
            m = X[idx, node["f"]] <= node["t"]
            stack.append((node["l"], idx[m]))
            stack.append((node["r"], idx[~m]))
        return out

    def fit(self, X, y, w):
        wn = w / w.mean()
        pw = float(np.clip((wn * y).sum() / max(1e-9, wn.sum()), 1e-4, 1 - 1e-4))
        self.base_ = float(np.log(pw / (1 - pw)))
        F = np.full(len(y), self.base_)
        self.trees_ = []
        for _ in range(self.n_trees):
            p = _sigmoid(F)
            g = wn * (p - y)
            h = wn * np.clip(p * (1 - p), 1e-6, None)
            tree = self._build(X, g, h, np.arange(len(y)), 0)
            self.trees_.append(tree)
            F += self.lr * self._apply(tree, X)
        return self

    def predict_proba(self, X):
        F = np.full(len(X), self.base_)
        for t in self.trees_:
            F += self.lr * self._apply(t, X)
        return _sigmoid(F)

    def to_dict(self):
        return dict(kind="gbm", base=self.base_, lr=self.lr, trees=self.trees_)


def load_model(d):
    if d["kind"] == "logistic":
        m = LogisticModel()
        m.mean_ = np.array(d["mean"])
        m.std_ = np.array(d["std"])
        m.w_ = np.array(d["weights"])
        m.b_ = d["bias"]
        return m
    m = GBMModel()
    m.base_, m.lr, m.trees_ = d["base"], d["lr"], d["trees"]
    return m


# ──────────────────────────────────────────────────────────────────────────
#  Evaluation helpers
# ──────────────────────────────────────────────────────────────────────────

def _average_precision(y, s):
    o = np.argsort(-s)
    y = y[o]
    tp = np.cumsum(y)
    prec = tp / np.arange(1, len(y) + 1)
    n_pos = max(1, int(y.sum()))
    return float((prec * y).sum() / n_pos)


def _auc(y, s):
    o = np.argsort(s)
    r = np.empty(len(s), float)
    r[o] = np.arange(1, len(s) + 1)
    # average ranks for ties
    for v in np.unique(s):
        m = s == v
        if m.sum() > 1:
            r[m] = r[m].mean()
    n1 = float(y.sum())
    n0 = float(len(y) - n1)
    if n1 == 0 or n0 == 0:
        return 0.5
    return float((r[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def _grouped_folds(groups, k, seed=0):
    uniq = np.array(sorted(set(groups)))
    rng = np.random.default_rng(seed)
    rng.shuffle(uniq)
    assign = {g: i % k for i, g in enumerate(uniq)}
    return np.array([assign[g] for g in groups])


def _pick_threshold(y, s, target_recall=TARGET_RECALL):
    """Strictest threshold that still retains `target_recall` of the human's
    own picks. Returned precision is a lower bound (negatives are unlabelled,
    not confirmed-bad), so it is reported for information only."""
    order = np.argsort(-s)
    ys = y[order]
    tp = np.cumsum(ys)
    prec = tp / np.arange(1, len(ys) + 1)
    rec = tp / max(1, int(y.sum()))
    ok = np.flatnonzero(rec >= target_recall)
    i = int(ok[0]) if len(ok) else int(np.argmax(rec))
    return float(s[order][i]), float(prec[i]), float(rec[i])


# ──────────────────────────────────────────────────────────────────────────
#  Train
# ──────────────────────────────────────────────────────────────────────────

def train(root, k=5, drop_isolation=False, out_name=None):
    """Train the picker.

    drop_isolation=True builds the variant for confluent monolayers, where
    "is this cell on its own?" carries no information because no cell is.
    """
    import pandas as pd

    root = os.path.abspath(root)
    ws = LC.workspace(root)
    tpath = os.path.join(ws, TRAIN_CSV)
    if not os.path.isfile(tpath):
        raise SystemExit(f"No {TRAIN_CSV}. Run with --build first.")
    df = pd.read_csv(tpath)

    feats = [c for c in LC.FEATURE_COLS if c in df.columns]
    if drop_isolation:
        feats = [c for c in feats if c not in ISOLATION_FEATURES]
        print("[train] CONFLUENT variant: ignoring "
              f"{', '.join(ISOLATION_FEATURES)}")
        # Hiding the isolation FEATURES is not enough. Many negatives are
        # perfectly healthy cells the human skipped only because they touched a
        # neighbour; left in, the model would rediscover "in contact = bad"
        # through correlated shape features, which is precisely the rule that
        # must not apply to a monolayer. Those negatives are removed, leaving
        # negatives that were rejected on their own merits - dying, debris,
        # clipped by the frame.
        if "touch_frac" in df.columns:
            drop = (df["is_pick"] == 0) & (df["touch_frac"] > 0.15)
            print(f"[train] dropped {int(drop.sum())} negatives that were "
                  f"rejected only for touching a neighbour")
            df = df[~drop].reset_index(drop=True)
    X = np.nan_to_num(df[feats].to_numpy(float), nan=0.0, posinf=0.0, neginf=0.0)
    y = df["is_pick"].to_numpy(int)
    w = df["w"].to_numpy(float)
    groups = (df["condition"] + "||" + df["file"]).to_numpy()

    n_img = len(set(groups))
    print(f"[train] {len(df)} objects from {n_img} images "
          f"({int(y.sum())} positive / {int((y==0).sum())} negative)")

    # A model fitted to a handful of examples is worse than the shipped one,
    # which was cross-validated on 975. Refuse rather than quietly replace it;
    # the caller keeps the existing picker and the corrections still apply.
    if int(y.sum()) < MIN_TRAIN_PER_CLASS or int((y == 0).sum()) < MIN_TRAIN_PER_CLASS:
        raise NotEnoughLabels(
            f"Only {int(y.sum())} good and {int((y==0).sum())} rejected cells - "
            f"at least {MIN_TRAIN_PER_CLASS} of each are needed to train a "
            f"picker for this dataset.\n\n"
            f"Your corrections are still applied to the results either way; "
            f"this only affects whether the picker itself learns from them.")
    print(f"[train] {len(feats)} brightfield features (no dye features by design)")

    fold = _grouped_folds(groups, min(k, n_img))
    candidates = [("logistic", LogisticModel()), ("gbm", GBMModel())]
    results = {}

    for name, proto in candidates:
        oof = np.zeros(len(y))
        for f in range(fold.max() + 1):
            te = fold == f
            tr = ~te
            if y[tr].sum() < 5 or (y[tr] == 0).sum() < 5:
                continue
            m = (LogisticModel() if name == "logistic" else GBMModel())
            m.fit(X[tr], y[tr], w[tr])
            oof[te] = m.predict_proba(X[te])
        ap = _average_precision(y, oof)
        auc = _auc(y, oof)
        thr, prec, rec = _pick_threshold(y, oof)
        results[name] = dict(oof=oof, ap=ap, auc=auc, thr=thr, prec=prec, rec=rec)
        print(f"[cv]  {name:9s}  AP={ap:.3f}  AUC={auc:.3f}   "
              f"@thr={thr:.3f}: precision={prec*100:.1f}% recall={rec*100:.1f}%")

    best_name = max(results, key=lambda n: results[n]["ap"])
    best = results[best_name]
    print(f"\n[train] chosen model: {best_name} (highest grouped-CV average precision)")

    model = (LogisticModel() if best_name == "logistic" else GBMModel())
    model.fit(X, y, w)

    if best_name == "logistic":
        order = np.argsort(-np.abs(model.w_))
        print("\n[train] feature weights (+ = pushes toward 'I would circle this'):")
        for i in order[:12]:
            print(f"    {feats[i]:>18s}  {model.w_[i]:+.3f}")
    else:
        used = np.zeros(len(feats))

        def _walk(nd, depth=0):
            if "leaf" in nd:
                return
            used[nd["f"]] += 1.0 / (1 + depth)
            _walk(nd["l"], depth + 1)
            _walk(nd["r"], depth + 1)
        for t in model.trees_:
            _walk(t)
        order = np.argsort(-used)
        print("\n[train] most-used split features (what the model actually looks at):")
        for i in order[:12]:
            if used[i] > 0:
                print(f"    {feats[i]:>18s}  {used[i]:.1f}")

    out = dict(
        features=feats, threshold=best["thr"],
        variant=("confluent" if drop_isolation else "sparse"),
        cv_average_precision=best["ap"], cv_auc=best["auc"],
        cv_precision=best["prec"], cv_recall=best["rec"],
        n_positive=int(y.sum()), n_negative=int((y == 0).sum()),
        n_images=n_img, target_recall=TARGET_RECALL,
        model=model.to_dict(),
    )
    mpath = os.path.join(ws, out_name or MODEL_JSON)
    with open(mpath, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2)

    df["oof_score"] = best["oof"]
    df.to_csv(tpath, index=False)

    print(f"\n[train] threshold {best['thr']:.3f} -> "
          f"precision {best['prec']*100:.1f}%, recall {best['rec']*100:.1f}%")
    print(f"[train] model -> {mpath}")
    return out


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print('Usage: python -m cell_viability.pick_model "<dataset>" [--build]')
        sys.exit(1)
    if "--build" in sys.argv:
        build_training_set(args[0])
    else:
        drop = "--confluent" in sys.argv
        name = None
        for a in sys.argv[1:]:
            if a.startswith("--out="):
                name = a.split("=", 1)[1]
        train(args[0], drop_isolation=drop, out_name=name)
