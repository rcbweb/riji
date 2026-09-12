# cell_viability/__init__.py
"""
Find the brightest LIVE cells, N per experiment folder.

The pipeline, in order:

  1. annotate_app  - you draw polygons around cells worth using. This is the
                     only place human judgement enters, and it is captured
                     once rather than repeated per dataset.
  2. livecell      - segments whole cells from the BRIGHTFIELD channel
                     (Cellpose-SAM, cached), describes each with shape /
                     refractility / texture / isolation features, and measures
                     whole-cell dye uptake against a local background ring.
  3. pick_model    - learns "would the human circle this?" from those polygons,
                     using brightfield features only.
  4. rank_top      - selects live cells, measures them, and ranks WITHIN EACH
                     FOLDER, because each folder is a separate experiment.

The key design rule: brightness NEVER decides what counts as a cell. Cell
identity comes from brightfield morphology; brightness only ranks the cells
that already passed. Doing it the other way round - the original approach -
preferentially selects dead cells, because dead cells pool dye and are the
brightest objects in the frame.

Submodules are imported lazily so that importing this package does not pull in
torch/cellpose until segmentation is actually needed.
"""
__all__ = ["livecell", "pick_model", "rank_top", "annotate_app", "report", "figure_editor", "review_app", "coloc_live", "session", "viability"]


def __getattr__(name):
    if name in __all__:
        import importlib
        return importlib.import_module(f".{name}", __name__)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
