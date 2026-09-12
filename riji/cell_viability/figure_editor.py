"""
figure_editor.py - fix the figure without re-running the analysis.
==================================================================
Opens live_cells.csv and lets you change everything a reviewer or a supervisor
will ask you to change, with a live preview:

  * rename conditions            "SUVs+anti PD-L1 3h" -> "anti-PD-L1"
  * reorder bars                 put the control first, where it belongs
  * hide a condition             without deleting any data
  * recolour bars
  * drop a bad image             out of focus, bubble, wrong field - the cells
                                 from that image leave every calculation
  * SD / SEM / 95% CI
  * n = cells  or  n = images    the conservative biological-replicate version
  * normalise to a control       axis becomes fold-change instead of a.u.
  * points / significance / n labels on or off
  * fix the y-axis maximum       so panels of a multi-panel figure match

Then export PNG + PDF + Excel again. Settings are saved next to the results as
figure_settings.json, so the exact figure can be regenerated later - which is
what you want when the paper comes back for revision six months from now.

Drawing is done by report.draw_bars, the same function the exporter uses, so
the preview is never a different picture from the file you ship.

Run:  python -m cell_viability.figure_editor "<results folder>"
"""
import os
import sys
import json
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cell_viability import report as RP   # noqa: E402
from cell_viability import outputs as OUT  # noqa: E402

SETTINGS_NAME = "figure_settings.json"


def run(path, master=None):
    import tkinter as tk
    from tkinter import ttk, messagebox
    import pandas as pd
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

    embedded = master is not None
    live_csv = OUT.find_live_cells(path)
    # The RESULTS folder, not the folder the table happens to sit in. Deriving
    # it from live_cells.csv sent exports into data/ while the dialog claimed
    # otherwise, so an edited figure never appeared where it was expected.
    outdir = OUT.results_root(live_csv)
    df = pd.read_csv(live_csv)

    metrics = [m for m in ("cy3_integrated", "cy3_mean") if m in df.columns]
    if not metrics:
        raise SystemExit("live_cells.csv has no intensity column.")

    all_conds = list(dict.fromkeys(df["condition"]))
    # conditions with no dye at all cannot go on an intensity axis
    signal = {c: bool(np.isfinite(df.loc[df.condition == c, metrics[0]]).any()
                      and (df.loc[df.condition == c, metrics[0]].fillna(0) > 0).any())
              for c in all_conds}

    S = dict(order=[c for c in all_conds if signal[c]],
             include={c: True for c in all_conds if signal[c]},
             labels={c: c for c in all_conds},
             colors={c: RP.DEFAULT_COLORS[i % len(RP.DEFAULT_COLORS)]
                     for i, c in enumerate(all_conds)},
             exclude=[], metric=metrics[0], error="sd", level="cell",
             plot_scope="top",
             normalize_to="", title="", xlabel="", ymax="",
             show_points=True, show_stars=True, show_n=False)

    sp = os.path.join(OUT.figures_dir(outdir, create=True), SETTINGS_NAME)
    if os.path.isfile(sp):
        try:
            with open(sp, encoding="utf-8") as fh:
                saved = json.load(fh)
            # Only accept a saved value if it has the shape we expect. A file
            # written by an older version (or hand-edited) must not be able to
            # stop the editor from opening.
            for k, v in saved.items():
                if k in S and isinstance(v, type(S[k])):
                    S[k] = v
            S["exclude"] = [tuple(e) for e in S.get("exclude", [])
                            if isinstance(e, (list, tuple)) and len(e) == 2]
            S["order"] = [c for c in S["order"] if c in all_conds] + \
                         [c for c in all_conds
                          if signal[c] and c not in S["order"]]
            for c in all_conds:
                S["include"].setdefault(c, True)
                S["labels"].setdefault(c, c)
                S["colors"].setdefault(c, RP.DEFAULT_COLORS[0])
            if S["normalize_to"] not in all_conds:
                S["normalize_to"] = ""
            if S["metric"] not in metrics:
                S["metric"] = metrics[0]
        except Exception as exc:
            print(f"[editor] ignoring unreadable {SETTINGS_NAME}: {exc}")

    win = tk.Toplevel(master) if embedded else tk.Tk()
    win.title("Figure editor")
    win.geometry("1440x920")

    left = tk.Frame(win)
    left.pack(side="left", fill="both", expand=True, padx=8, pady=8)
    fig = Figure(figsize=(7.2, 5.6), dpi=100)
    canvas = FigureCanvasTkAgg(fig, master=left)
    canvas.get_tk_widget().pack(fill="both", expand=True)

    right = tk.Frame(win, width=480)
    right.pack(side="right", fill="y", padx=(0, 8), pady=8)
    right.pack_propagate(False)
    nb = ttk.Notebook(right)
    nb.pack(fill="both", expand=True)
    tab_bars = tk.Frame(nb)
    tab_data = tk.Frame(nb)
    tab_style = tk.Frame(nb)
    nb.add(tab_bars, text="Bars")
    nb.add(tab_data, text="Data")
    nb.add(tab_style, text="Style")

    status = tk.Label(right, text="", anchor="w", fg="#333", wraplength=456,
                      justify="left")
    status.pack(fill="x", pady=(6, 0))

    # ---------------- redraw ----------------
    def current_series():
        conds, data, note = RP.series_from_df(
            df, S["metric"], scope=S.get("plot_scope", "top"),
            level=S["level"],
            exclude_images=set(S["exclude"]),
            normalize_to=S["normalize_to"] or None,
            order=S["order"],
            include=[c for c in S["order"] if S["include"].get(c, True)])
        return conds, data, note

    # Redraws are debounced. Re-rendering on every keystroke made the window
    # stop responding while typing a label; now the last change within the
    # idle window is the one that draws.
    _ui = {"job": None}

    def schedule_redraw(delay=220):
        if _ui["job"] is not None:
            try:
                win.after_cancel(_ui["job"])
            except Exception:
                pass
        _ui["job"] = win.after(delay, redraw)

    def redraw(*_):
        _ui["job"] = None
        fig.clear()
        ax = fig.add_subplot(111)
        conds, data, note = current_series()
        try:
            RP.draw_bars(
                ax, conds, data,
                labels=[S["labels"].get(c, c) for c in conds],
                colors=[S["colors"].get(c, "#B3B3B3") for c in conds],
                error=S["error"], show_points=S["show_points"],
                show_stars=S["show_stars"], show_n=S["show_n"],
                title=S["title"], xlabel=S["xlabel"], metric=S["metric"],
                unit_note=note,
                ymax=float(S["ymax"]) if str(S["ymax"]).strip() else None)
        except Exception as exc:
            ax.clear()
            ax.text(0.5, 0.5, f"Cannot draw:\n{exc}", ha="center", va="center",
                    color="#a00", wrap=True)
            ax.axis("off")
        fig.tight_layout()
        canvas.draw()
        n_txt = ", ".join(f"{S['labels'].get(c, c)}={len(v)}"
                          for c, v in zip(conds, data))
        status.config(text=f"n per bar ({S['level']}): {n_txt}"
                           + (f"   |  {len(S['exclude'])} image(s) excluded"
                              if S["exclude"] else ""))

    # ---------------- Bars tab ----------------
    tk.Label(tab_bars, text="Rename, recolour, reorder, or hide each bar.",
             fg="#444", wraplength=400, justify="left").pack(anchor="w", padx=8,
                                                             pady=(8, 4))
    rows_holder = tk.Frame(tab_bars)
    rows_holder.pack(fill="both", expand=True, padx=6)

    def rebuild_rows():
        for w in rows_holder.winfo_children():
            w.destroy()
        for i, cond in enumerate(S["order"]):
            fr = tk.Frame(rows_holder)
            fr.pack(fill="x", pady=3)

            var_inc = tk.BooleanVar(value=S["include"].get(cond, True))

            def _tog(c=cond, v=var_inc):
                S["include"][c] = v.get()
                redraw()
            tk.Checkbutton(fr, variable=var_inc, command=_tog).pack(side="left")

            ent = tk.Entry(fr, width=16)
            ent.insert(0, S["labels"].get(cond, cond))

            def _rename(_e=None, c=cond, e=None):
                S["labels"][c] = (e.get().strip() or c)
                schedule_redraw()
            ent.bind("<KeyRelease>",
                     lambda e, c=cond, w=ent: (_rename(c=c, e=w)))
            ent.pack(side="left", padx=3)

            # Colour is a plain list, not a modal RGB dialog. The dialog was
            # application-modal, so while it was open the whole editor showed
            # as "Not Responding".
            sw = tk.Label(fr, text="  ", bg=S["colors"].get(cond, "#B3B3B3"),
                          relief="ridge", width=2)
            sw.pack(side="left", padx=(3, 1))
            cur_hex = S["colors"].get(cond, "#B3B3B3").upper()
            v_col = tk.StringVar(value=RP.PALETTE_BY_HEX.get(cur_hex, "Custom"))
            cbc = ttk.Combobox(fr, textvariable=v_col, state="readonly", width=9,
                               values=[n for n, _ in RP.PALETTE])

            def _colour(_e=None, c=cond, b=sw, v=v_col):
                hexv = RP.PALETTE_BY_NAME.get(v.get())
                if hexv:
                    S["colors"][c] = hexv
                    b.configure(bg=hexv)
                    schedule_redraw(60)
            cbc.bind("<<ComboboxSelected>>", _colour)
            cbc.pack(side="left", padx=1)

            def _up(c=cond):
                i = S["order"].index(c)
                if i > 0:
                    S["order"][i - 1], S["order"][i] = S["order"][i], S["order"][i - 1]
                    rebuild_rows()
                    redraw()

            def _down(c=cond):
                i = S["order"].index(c)
                if i < len(S["order"]) - 1:
                    S["order"][i + 1], S["order"][i] = S["order"][i], S["order"][i + 1]
                    rebuild_rows()
                    redraw()
            tk.Button(fr, text="^", width=2, command=_up).pack(side="left")
            tk.Button(fr, text="v", width=2, command=_down).pack(side="left")
    rebuild_rows()

    # ---------------- Data tab ----------------
    def add_row(parent, text):
        fr = tk.Frame(parent)
        fr.pack(fill="x", padx=8, pady=4)
        tk.Label(fr, text=text, width=14, anchor="w").pack(side="left")
        return fr

    fr = add_row(tab_data, "Measure:")
    v_metric = tk.StringVar(value=S["metric"])
    ttk.Combobox(fr, textvariable=v_metric, values=metrics, state="readonly",
                 width=20).pack(side="left")
    tk.Label(tab_data, text="cy3_integrated = total dye per cell (size-dependent).\n"
                            "cy3_mean = per unit area (controls for cell size).",
             fg="#666", font=("Segoe UI", 8), justify="left").pack(anchor="w", padx=10)

    fr = add_row(tab_data, "Each point is:")
    v_level = tk.StringVar(value=S["level"])
    ttk.Combobox(fr, textvariable=v_level, values=["cell", "image"],
                 state="readonly", width=20).pack(side="left")
    tk.Label(tab_data,
             text="'image' gives each image one mean - the conservative\n"
                  "biological-replicate n. Use it if a reviewer questions n.",
             fg="#666", font=("Segoe UI", 8), justify="left").pack(anchor="w", padx=10)

    fr = add_row(tab_data, "Show:")
    v_scope = tk.StringVar(value=("Top N per folder (as exported)"
                                  if S.get("plot_scope", "top") == "top"
                                  else "All selected cells"))
    ttk.Combobox(fr, textvariable=v_scope, state="readonly", width=26,
                 values=["Top N per folder (as exported)",
                         "All selected cells"]).pack(side="left")
    tk.Label(tab_data,
             text="'Top N' shows exactly the cells exported as pictures.\n"
                  "'All selected' shows the whole population the filter kept -\n"
                  "more cells, and the statistics can differ.",
             fg="#666", font=("Segoe UI", 8), justify="left").pack(anchor="w",
                                                                   padx=10)

    fr = add_row(tab_data, "Error bars:")
    v_err = tk.StringVar(value=S["error"])
    ttk.Combobox(fr, textvariable=v_err, values=["sd", "sem", "ci95"],
                 state="readonly", width=20).pack(side="left")

    fr = add_row(tab_data, "Normalise to:")
    v_norm = tk.StringVar(value=S["normalize_to"])
    ttk.Combobox(fr, textvariable=v_norm,
                 values=[""] + [c for c in all_conds if signal[c]],
                 state="readonly", width=20).pack(side="left")
    tk.Label(tab_data, text="Turns the axis into fold-change vs that condition.",
             fg="#666", font=("Segoe UI", 8)).pack(anchor="w", padx=10)

    tk.Label(tab_data, text="Exclude images (bad field, out of focus, bubble):",
             anchor="w").pack(fill="x", padx=8, pady=(12, 2))
    lb_frame = tk.Frame(tab_data)
    lb_frame.pack(fill="both", expand=True, padx=8)
    sb = tk.Scrollbar(lb_frame)
    sb.pack(side="right", fill="y")
    lb = tk.Listbox(lb_frame, selectmode="multiple", yscrollcommand=sb.set,
                    height=11, font=("Consolas", 8))
    lb.pack(side="left", fill="both", expand=True)
    sb.config(command=lb.yview)

    pairs = sorted({(str(a), str(b)) for a, b in zip(df["condition"], df["file"])})
    for i, (c, f) in enumerate(pairs):
        n_here = int(((df.condition == c) & (df.file == f)).sum())
        lb.insert("end", f"{c[:22]:22s} {f[:18]:18s} ({n_here} cells)")
        if (c, f) in set(S["exclude"]):
            lb.selection_set(i)

    def apply_exclusions():
        S["exclude"] = [pairs[i] for i in lb.curselection()]
        redraw()
    tk.Button(tab_data, text="Apply exclusions", command=apply_exclusions
              ).pack(pady=6)

    # ---------------- Style tab ----------------
    entries = {}
    for key, label in (("title", "Title:"), ("xlabel", "X axis label:"),
                       ("ymax", "Y max (blank=auto):")):
        fr = add_row(tab_style, label)
        e = tk.Entry(fr, width=22)
        e.insert(0, str(S[key]))
        e.pack(side="left")
        entries[key] = e

    for key, label in (("show_points", "Show individual points"),
                       ("show_stars", "Show significance stars"),
                       ("show_n", "Show n under each bar")):
        v = tk.BooleanVar(value=S[key])

        def _t(k=key, vv=None):
            S[k] = vv.get()
            redraw()
        cb = tk.Checkbutton(tab_style, text=label, variable=v,
                            command=lambda k=key, vv=v: _t(k, vv))
        cb.pack(anchor="w", padx=10, pady=2)

    def apply_style():
        for k, e in entries.items():
            S[k] = e.get().strip()
        redraw()
    tk.Button(tab_style, text="Apply text / axis settings",
              command=apply_style).pack(pady=8)

    def on_change(*_):
        S["metric"] = v_metric.get()
        S["level"] = v_level.get()
        S["error"] = v_err.get()
        S["normalize_to"] = v_norm.get()
        S["plot_scope"] = ("top" if v_scope.get().startswith("Top") else "all")
        redraw()
    for v in (v_metric, v_level, v_err, v_norm, v_scope):
        v.trace_add("write", on_change)

    # ---------------- export ----------------
    bar = tk.Frame(right)
    bar.pack(fill="x", pady=8)

    def save_settings():
        with open(sp, "w", encoding="utf-8") as fh:
            json.dump({k: (list(map(list, v)) if k == "exclude" else v)
                       for k, v in S.items()}, fh, indent=2)

    def export():
        try:
            apply_style()
            save_settings()
            st = dict(S)
            st["include"] = [c for c in S["order"] if S["include"].get(c, True)]
            st["exclude"] = [list(e) for e in S["exclude"]]
            fig_path, xlsx_path = RP.build_report(
                live_csv, outdir=outdir, metric=S["metric"], settings=st)
            # Report the folder the files were actually written to. This used
            # to name a folder it had worked out separately, so when the two
            # disagreed the dialog confidently pointed at the wrong place.
            written = os.path.dirname(os.path.abspath(fig_path))
            names = sorted({os.path.basename(fig_path),
                            os.path.splitext(os.path.basename(fig_path))[0] + ".pdf",
                            os.path.basename(xlsx_path),
                            OUT.METHODS, SETTINGS_NAME})
            messagebox.showinfo(
                "Exported",
                "Written to:\n" + written + "\n\n" + "\n".join(names))
        # SystemExit is not an Exception. It is how the report says "stop,
        # here is why" - a missing table, a workbook still open in Excel - and
        # it used to travel straight through Tk and close the whole app.
        except SystemExit as stop:
            messagebox.showerror("Export stopped", str(stop)
                                 or "The export stopped without a reason.")
        except Exception as exc:
            messagebox.showerror("Export failed", str(exc))

    tk.Button(bar, text="Export figure + Excel", command=export,
              bg="#d4edda", height=2).pack(fill="x", pady=2)
    tk.Button(bar, text="Save settings only", command=save_settings).pack(fill="x")
    tk.Label(right, text=f"Results folder:\n{outdir}", fg="#888",
             font=("Segoe UI", 7), wraplength=456, justify="left").pack(
                 anchor="w", pady=(6, 0))

    redraw()
    if embedded:
        win.transient(master)
        win.focus_force()
        return win          # the caller's event loop is already running
    win.mainloop()


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print('Usage: python -m cell_viability.figure_editor "<results folder>"')
        sys.exit(1)
    run(args[0])
