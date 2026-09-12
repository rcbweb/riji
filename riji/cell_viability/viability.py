"""
viability.py - is this cell alive or dead?
===========================================
A DIFFERENT question from the one pick_model answers.

  pick_model : "would the human circle this cell for measurement?"
               - alive, spread, isolated, whole, not clipped by the frame.
  viability  : "is this cell alive or dead?"
               - purely about the cell's state, regardless of whether it is
                 touching a neighbour or sitting on the edge of the field.

The distinction matters. A cell the picker rejects is NOT necessarily dead: it
may be perfectly healthy but overlapping a neighbour. So "everything the picker
threw away" cannot be used as a dead-cell population - it would be mostly live
cells with awkward neighbours. Studying dead cells needs its own classifier
trained on cells a human actually called dead.

Training data comes from hand-scored cells (live / dead), matched onto the
current segmentation by centroid. Evaluation is grouped by image, so an image
never appears in both train and test.

Run:
    python -m cell_viability.viability "<dataset>" --build   # gather labels
    python -m cell_viability.viability "<dataset>"           # train + report
"""
import os
import sys
import json
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cell_viability import livecell as LC          # noqa: E402
from cell_viability import pick_model as PM        # noqa: E402

TRAIN_CSV = "viability_training.csv"
MODEL_JSON = "viability_model.json"
DEFAULT_MODEL = LC.package_file("default_dead_model.json")

# Dead cells are the minority class and the expensive mistake is calling a live
# cell dead, so the threshold targets recall of confirmed dead cells while the
# reported precision keeps the cost visible.
TARGET_DEAD_RECALL = 0.75


def _label_sources(ws):
    """Hand-scored cells, from whichever file in the workspace holds them."""
    import pandas as pd
    for name in ("dead_labels_matched.csv", "catalog.csv"):
        p = os.path.join(ws, name)
        if not os.path.isfile(p):
            continue
        df = pd.read_csv(p)
        if "label" not in df.columns:
            continue
        df["label"] = df["label"].fillna("").astype(str).str.strip().str.lower()
        df = df[df["label"].isin(["live", "dead"])]
        if len(df):
            return df, name
    return None, None


def build_training(root):
    """Attach the current features to every hand-scored cell."""
    import pandas as pd

    root = os.path.abspath(root)
    ws = LC.workspace(root)
    lab, src_name = _label_sources(ws)
    if lab is None:
        raise SystemExit(
            "No live/dead scores found in this dataset.\n"
            "Score some cells as live or dead first, or use the built-in model.")
    print(f"[viability] {len(lab)} hand-scored cells from {src_name}: "
          f"{lab['label'].value_counts().to_dict()}")

    by_img = {}
    for it in LC.list_images(root):
        by_img[(it["condition"], it["file"])] = it

    # An object marked "not a cell" in the review window is neither live nor
    # dead. An old live/dead score for it is about a piece of debris, and
    # training on it would teach the model that debris is a dead cell.
    from cell_viability import rank_top as RT
    cur = RT.load_curation(ws)

    rows, missed, junked = [], 0, 0
    for (cond, fname), g in lab.groupby(["condition", "file"]):
        it = by_img.get((cond, fname))
        if it is None:
            missed += len(g)
            continue
        junk = RT.discarded_ids(cur, cond, fname)
        try:
            bf, _dye, px = LC.load_channels(it["path"])
            masks = LC.cached_masks(it["path"], cond, fname, ws, verbose=False)
            feats, _k = LC.cell_features(masks, bf, px)
        except Exception:
            missed += len(g)
            continue
        by_id = {f["_label_id"]: f for f in feats}

        for _, r in g.iterrows():
            lid = None
            if "label_id" in r and not pd.isna(r["label_id"]):
                lid = int(r["label_id"])
            else:                       # fall back to the recorded centroid
                x, y = int(r.get("x", -1)), int(r.get("y", -1))
                if 0 <= y < masks.shape[0] and 0 <= x < masks.shape[1]:
                    lid = int(masks[y, x]) or None
            if lid in junk:
                junked += 1
                continue
            f = by_id.get(lid) if lid else None
            if f is None:
                missed += 1
                continue
            rows.append(dict(condition=cond, file=fname, label_id=lid,
                             is_dead=int(r["label"] == "dead"),
                             **{k: f[k] for k in LC.FEATURE_COLS}))

    if not rows:
        raise SystemExit("None of the scored cells could be matched to a cell.")
    df = pd.DataFrame(rows).drop_duplicates(["condition", "file", "label_id"])
    out = os.path.join(ws, TRAIN_CSV)
    df.to_csv(out, index=False)
    print(f"[viability] matched {len(df)} cells "
          f"({int(df.is_dead.sum())} dead / {int((df.is_dead == 0).sum())} live)"
          + (f", {missed} unmatched" if missed else "")
          + (f", {junked} skipped as not a cell" if junked else ""))
    print(f"[viability] -> {out}")
    return out


def train(root, k=5, out_name=None):
    import pandas as pd

    root = os.path.abspath(root)
    ws = LC.workspace(root)
    tpath = os.path.join(ws, TRAIN_CSV)
    if not os.path.isfile(tpath):
        build_training(root)
    df = pd.read_csv(tpath)

    feats = [c for c in LC.FEATURE_COLS if c in df.columns]
    X = np.nan_to_num(df[feats].to_numpy(float), nan=0.0, posinf=0.0, neginf=0.0)
    y = df["is_dead"].to_numpy(int)
    w = np.ones(len(y))
    groups = (df["condition"] + "||" + df["file"]).to_numpy()
    n_img = len(set(groups))

    print(f"[viability] {len(df)} cells from {n_img} images "
          f"({int(y.sum())} dead / {int((y == 0).sum())} live)")
    if int(y.sum()) < PM.MIN_TRAIN_PER_CLASS or \
       int((y == 0).sum()) < PM.MIN_TRAIN_PER_CLASS:
        raise PM.NotEnoughLabels(
            f"Only {int(y.sum())} dead and {int((y==0).sum())} live scored "
            f"cells - at least {PM.MIN_TRAIN_PER_CLASS} of each are needed.")

    fold = PM._grouped_folds(groups, min(k, n_img))
    results = {}
    for name in ("logistic", "gbm"):
        oof = np.zeros(len(y))
        for f in range(fold.max() + 1):
            te, tr = fold == f, fold != f
            if y[tr].sum() < 5 or (y[tr] == 0).sum() < 5:
                continue
            m = PM.LogisticModel() if name == "logistic" else PM.GBMModel()
            m.fit(X[tr], y[tr], w[tr])
            oof[te] = m.predict_proba(X[te])
        ap, auc = PM._average_precision(y, oof), PM._auc(y, oof)
        thr, prec, rec = PM._pick_threshold(y, oof, TARGET_DEAD_RECALL)
        results[name] = dict(oof=oof, ap=ap, auc=auc, thr=thr, prec=prec, rec=rec)
        print(f"[cv]  {name:9s} AP={ap:.3f} AUC={auc:.3f}   @thr={thr:.3f}: "
              f"precision={prec*100:.1f}% recall={rec*100:.1f}%")

    best_name = max(results, key=lambda n: results[n]["ap"])
    best = results[best_name]
    print(f"\n[viability] chosen: {best_name}  "
          f"(of the cells it calls dead, {best['prec']*100:.0f}% really were; "
          f"it finds {best['rec']*100:.0f}% of the dead ones)")

    model = PM.LogisticModel() if best_name == "logistic" else PM.GBMModel()
    model.fit(X, y, w)

    out = dict(features=feats, threshold=best["thr"], variant="viability",
               positive_class="dead",
               cv_average_precision=best["ap"], cv_auc=best["auc"],
               cv_precision=best["prec"], cv_recall=best["rec"],
               n_dead=int(y.sum()), n_live=int((y == 0).sum()),
               n_images=n_img, model=model.to_dict())
    mpath = os.path.join(ws, out_name or MODEL_JSON)
    with open(mpath, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2)
    print(f"[viability] model -> {mpath}")
    return out


def load_model(ws=None):
    """Dataset-specific model if one exists, else the one that ships."""
    if ws:
        local = os.path.join(ws, MODEL_JSON)
        if os.path.isfile(local):
            with open(local, encoding="utf-8") as fh:
                return json.load(fh), "this dataset's own live/dead scores"
    if os.path.isfile(DEFAULT_MODEL):
        with open(DEFAULT_MODEL, encoding="utf-8") as fh:
            return json.load(fh), "built-in dead-cell model"
    raise SystemExit(
        "No dead-cell model available.\n"
        "Score some cells live/dead in this dataset and train one:\n"
        '  python -m cell_viability.viability "<dataset>"')


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print('Usage: python -m cell_viability.viability "<dataset>" [--build]')
        sys.exit(1)
    if "--build" in sys.argv:
        build_training(args[0])
    else:
        name = None
        for a in sys.argv[1:]:
            if a.startswith("--out="):
                name = a.split("=", 1)[1]
        train(args[0], out_name=name)
