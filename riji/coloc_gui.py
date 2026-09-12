# RB
"""
===========================================================================
 coloc_gui.py  --  a simple window for coloc.py (no terminal needed)   RB
===========================================================================
 A plain window: choose your folder, type your dyes, tick what to run, press a
 button. It just drives coloc.py (which stays the engine), so the results are
 identical to running it from the command line.

 HOW TO RUN
   1) once:  pip install czifile scikit-image scipy numpy matplotlib pandas openpyxl
   2) double-click this file, or:   python coloc_gui.py

 Rchin was here :)   (please keep this credit)
===========================================================================
"""
import os
import sys
import time
import queue
import threading
import tkinter as tk
from tkinter import filedialog, ttk, scrolledtext
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import coloc

__author__ = "Rchin Bari"

BLANK_RECENT = "(none - type a folder above)"
BG = "#d9d9d9"          # plain grey
PAD = dict(padx=8, pady=4)


class App:
    def __init__(self, root):
        self.root = root
        self.q = queue.Queue()
        root.title("Riji")
        root.configure(bg=BG)

        tk.Label(root, text="Riji", bg=BG,
                 font=("Segoe UI", 15, "bold")).grid(
                     row=0, column=0, sticky="w", padx=8, pady=(8, 0))
        tk.Label(root,
                 text=("Choose your images folder and where results should go, "
                       "then press 1."),
                 bg=BG, fg="#444").grid(row=0, column=1, columnspan=2,
                                        sticky="w", padx=8, pady=(10, 0))

        # --- folder & output ---
        frm_paths = tk.Frame(root, bg=BG)
        frm_paths.grid(row=1, column=0, columnspan=3, sticky="we", **PAD)
        
        tk.Button(frm_paths, text="Choose folder...", command=self.pick, width=15).grid(row=0, column=0, sticky="w", pady=2)
        self.folder = tk.Entry(frm_paths, width=64)
        self.folder.grid(row=0, column=1, sticky="we", padx=(5, 0), pady=2)
        if getattr(coloc, "FOLDER", ""):
            self.folder.insert(0, getattr(coloc, "FOLDER", ""))
            
        tk.Button(frm_paths, text="Save results to...", command=self.pick_outdir, width=15).grid(row=1, column=0, sticky="w", pady=2)
        self.outdir = tk.Entry(frm_paths, width=64)
        self.outdir.grid(row=1, column=1, sticky="we", padx=(5, 0), pady=2)
        
        # We will track if the user manually modifies the output folder
        self._user_picked_outdir = False
        self.outdir.bind("<Key>", lambda e: setattr(self, "_user_picked_outdir", True))

        # --- recent projects: open on Monday where you stopped on Friday ---
        try:
            from cell_viability import session as _ss
            self._recent = _ss.recent_projects()
        except Exception:
            self._recent = []
        self._recent_labels = []
        if self._recent:
            tk.Label(frm_paths, text="Recent:", bg=BG).grid(
                row=2, column=0, sticky="w", pady=2)
            for p in self._recent:
                try:
                    from cell_viability import session as _ss2
                    prog = _ss2.project_progress(p["folder"])
                except Exception:
                    prog = ""
                self._recent_labels.append(
                    os.path.basename(p["folder"]) + "   [" + prog + "]   "
                    + p["folder"])
            self.recent_box = ttk.Combobox(
                frm_paths, values=[BLANK_RECENT] + self._recent_labels,
                width=62, state="readonly")
            self.recent_box.set(BLANK_RECENT)
            self.recent_box.grid(row=2, column=1, sticky="we", padx=(5, 0), pady=2)
            self.recent_box.bind("<<ComboboxSelected>>", self._use_recent)
            # Deliberately NOT filling the paths in from the last project.
            #
            # The window opened with the previous dataset already typed in
            # while the Recent box beside it read "(none)", so it contradicted
            # itself about whether anything was chosen. Worse, "1. Find and
            # measure cells" was then one click away from analysing yesterday's
            # folder - a filled box looks like a decision somebody made.
            # Starting empty means the only dataset that ever runs is one that
            # was chosen on purpose; Recent is one click away for the rest.

        # --- dyes ---
        frm_dyes = tk.Frame(root, bg=BG)
        frm_dyes.grid(row=3, column=0, columnspan=3, sticky="we", **PAD)
        tk.Label(frm_dyes, text="Dyes   (one per line, NOT case sensitive, e.g., 'Deep Red = lysosome')", bg=BG)\
            .pack(side="left")
        tk.Button(frm_dyes, text="[?] Dye Guide", command=self.show_dye_guide, bg="#e2e6ea", bd=1, cursor="hand2")\
            .pack(side="right", padx=(10, 10))
        tk.Button(frm_dyes, text="[?] Role Guide", command=self.show_role_guide, bg="#e2e6ea", bd=1, cursor="hand2")\
            .pack(side="right", padx=0)
        self.dyes = tk.Text(root, width=70, height=5)
        self.dyes.grid(row=4, column=0, columnspan=3, sticky="we", **PAD)
        self.dye_examples = [
            "Dye1 = Role1",
            "Dye2 = Role2",
            "Dye3 = Role3",
            "Dye4 = Role4",
            "Dye5 = Role5"
        ]
        self.dyes.insert("1.0", "\n".join(self.dye_examples))

        # --- colours ---
        c_frame = tk.Frame(root, bg=BG)
        c_frame.grid(row=5, column=0, columnspan=3, sticky="w", **PAD)
        tk.Label(c_frame, text="Colours   (optional, case-insensitive. e.g., 'lysosome = magenta')", bg=BG).pack(side="left")
        tk.Button(c_frame, text="Auto-fill from Dyes", command=self._autofill_colours,
                  bg="#e2e6ea", bd=1, cursor="hand2").pack(side="left", padx=6)
        tk.Button(c_frame, text="View Legal Colors", command=self.view_colors).pack(side="left", padx=4)
        
        self.colours = tk.Text(root, width=70, height=5)
        self.colours.grid(row=6, column=0, columnspan=3, sticky="we", **PAD)
        self.col_examples = [
            "Role1 = color1",
            "Role2 = color2",
            "Role3 = color3",
            "Role4 = color4",
            "Role5 = color5"
        ]
        self.colours.insert("1.0", "\n".join(self.col_examples))

        # --- custom graph titles ---
        frm_notes_hdr = tk.Frame(root, bg=BG)
        frm_notes_hdr.grid(row=7, column=0, columnspan=3, sticky="we", **PAD)
        tk.Label(frm_notes_hdr, text="Custom Graph Titles", bg=BG).pack(side="left")
        tk.Label(frm_notes_hdr, text="   Comparisons:", bg=BG).pack(side="left", padx=(20, 0))
        self._n_comparisons = tk.IntVar(value=0)
        tk.Spinbox(frm_notes_hdr, from_=0, to=20, width=3, textvariable=self._n_comparisons
                   ).pack(side="left", padx=4)
        tk.Button(frm_notes_hdr, text="Generate Rows", command=self._gen_note_rows,
                  bg="#e2e6ea", bd=1, cursor="hand2").pack(side="left", padx=6)
        # The "compute everything" option lives with the other run options, in
        # the window that opens when a colocalization button is pressed. The
        # variable is made here so both of those windows share one setting
        # rather than each keeping its own.
        self._all_pairs = tk.BooleanVar(value=False)

        self.notes_frame = tk.Frame(root, bg="white", bd=1, relief="sunken")
        self.notes_frame.grid(row=8, column=0, columnspan=3, sticky="we", **PAD)
        self.notes_frame.columnconfigure(3, weight=1)

        # Header labels
        tk.Label(self.notes_frame, text="  Role A", bg="#e8e8e8",
                 font=("Segoe UI", 9, "bold"), width=14, anchor="w"
                 ).grid(row=0, column=0, sticky="we", pady=1)
        tk.Label(self.notes_frame, text=" in ", bg="#e8e8e8",
                 font=("Segoe UI", 9, "bold"), width=3, anchor="center"
                 ).grid(row=0, column=1, sticky="we", pady=1)
        tk.Label(self.notes_frame, text="  Role B", bg="#e8e8e8",
                 font=("Segoe UI", 9, "bold"), width=14, anchor="w"
                 ).grid(row=0, column=2, sticky="we", pady=1)
        tk.Label(self.notes_frame, text="  Custom Title (optional)", bg="#e8e8e8",
                 font=("Segoe UI", 9, "bold"), anchor="w"
                 ).grid(row=0, column=3, sticky="we", pady=1)

        self._note_rows = []  # [(combo_a, combo_b, entry_title), ...]
        self._note_placeholder = tk.Label(
            self.notes_frame,
            text="   Set the number of comparisons above and click 'Generate Rows'.",
            bg="white", fg="grey", anchor="w")
        self._note_placeholder.grid(row=1, column=0, columnspan=4, sticky="w", padx=5, pady=8)

        # --- options ---
        opt = tk.Frame(root, bg=BG); opt.grid(row=9, column=0, columnspan=3, sticky="w", **PAD)
        tk.Label(opt, text="Thresholding:", bg=BG).grid(row=0, column=0, sticky="w", pady=2)
        self.thresh = ttk.Combobox(opt, values=["costes", "otsu"], width=9, state="readonly")
        self.thresh.set(getattr(coloc, "THRESH_METHOD", "costes")); self.thresh.grid(row=0, column=1, padx=(4, 16), pady=2)
        
        tk.Label(opt, text="Experiment title:", bg=BG).grid(row=0, column=2, sticky="w", pady=2)
        self.title = tk.Entry(opt, width=34); self.title.grid(row=0, column=3, padx=4, pady=2)
        self.title.bind("<KeyRelease>", self._update_outdir)

        tk.Label(opt, text="Cell ID Method:", bg=BG).grid(row=1, column=0, sticky="w", pady=2)
        self.segment = ttk.Combobox(opt, values=["cellpose", "otsu"], width=9, state="readonly")
        self.segment.set(getattr(coloc, "SEGMENT_METHOD", "cellpose"))
        self.segment.grid(row=1, column=1, padx=(4, 16), pady=2)

        # Everything above this point configures COLOCALIZATION only. The
        # cell-uptake workflow needs none of it, so it starts hidden: the
        # window opens showing the folders and the buttons, nothing else.
        self._coloc_widgets = [frm_dyes, self.dyes, c_frame, self.colours,
                               frm_notes_hdr, self.notes_frame, opt]
        self._coloc_shown = tk.BooleanVar(value=False)

        def _toggle_coloc():
            show = self._coloc_shown.get()
            for w in self._coloc_widgets:
                (w.grid() if show else w.grid_remove())

        tk.Checkbutton(root, variable=self._coloc_shown, bg=BG,
                       text="Show colocalization settings  "
                            "(dye names and colours - only needed for the two "
                            "colocalization buttons)",
                       command=_toggle_coloc).grid(row=2, column=0, columnspan=3,
                                                   sticky="w", padx=6)
        _toggle_coloc()          # start collapsed

        # --- run buttons ---
        bar = tk.Frame(root, bg=BG); bar.grid(row=10, column=0, columnspan=3, sticky="w", **PAD)
        # Three groups, divided by a rule. The numbered buttons are a sequence
        # you work through; the colocalization ones are a separate question you
        # may never ask, and only apply with two or more dyes. Run together in
        # one row they read as a five-step recipe, and "Colocalization in
        # cells" looks like step 4 that everyone is meant to press.
        groups = [
            [tk.Button(bar, text="1.  Find and measure cells",
                       command=self.popup_strongest, bg="#d4edda"),
             tk.Button(bar, text="2.  Check the cells",
                       command=self.open_review_app),
             tk.Button(bar, text="3.  Adjust the figure",
                       command=self.open_figure_editor)],
            [tk.Button(bar, text="Colocalization in cells",
                       command=self.run_coloc_live),
             tk.Button(bar, text="Colocalization (whole image)",
                       command=self.popup_coloc)],
            [tk.Button(bar, text="List dyes",
                       command=lambda: self.go(coloc.build_reference))],
        ]
        # stays a flat list of buttons: it is what gets disabled during a run
        self.buttons = [b for g in groups for b in g]
        for i, g in enumerate(groups):
            if i:
                ttk.Separator(bar, orient="vertical").pack(
                    side="left", fill="y", padx=9, pady=3)
            for b in g:
                b.pack(side="left", padx=4)
            
        self.btn_open_results = tk.Button(bar, text="Open Results Folder", command=self.open_results, state="disabled", bg="#d4edda")
        self.btn_open_results.pack(side="right", padx=16)

        # --- log + status ---
        self.log = scrolledtext.ScrolledText(root, width=92, height=16, state="disabled", bg="white")
        self.log.grid(row=11, column=0, columnspan=3, sticky="nsew", **PAD)
        self.status = tk.Label(root, text="Ready.", bg=BG, anchor="w")
        self.status.grid(row=12, column=0, columnspan=3, sticky="we", **PAD)
        self.spinner_idx = 0
        root.columnconfigure(1, weight=1)
        root.rowconfigure(11, weight=1)

    # ---- helpers ----
    def _autofill_colours(self):
        """Auto-fill roles from Dyes into the Colours box, preserving existing assignments."""
        roles = self._get_roles()
        if not roles:
            self._write("No roles found in Dyes to auto-fill.\n")
            return

        # Parse existing colours
        raw_cols = self.colours.get("1.0", "end").splitlines()
        existing = {}
        for c in raw_cols:
            c_str = c.strip()
            if c_str and c_str not in self.col_examples and "=" in c_str:
                role, color = c_str.split("=", 1)
                existing[role.strip()] = color.strip()

        # Build new text
        new_lines = []
        for r in roles:
            color = existing.get(r, "")
            new_lines.append(f"{r} = {color}")
        
        # Add any existing colors for roles not currently in Dyes (just in case they still want them)
        for r, color in existing.items():
            if r not in roles:
                new_lines.append(f"{r} = {color}")

        # Update text box
        self.colours.delete("1.0", "end")
        self.colours.insert("1.0", "\n".join(new_lines))
        self._write(f"Auto-filled {len(roles)} role(s) into Colours.\n")

    def _get_roles(self):
        """Parse role names from the Dyes text box."""
        raw = self.dyes.get("1.0", "end").splitlines()
        valid = [d for d in raw if d.strip() and d.strip() not in self.dye_examples and "=" in d]
        roles = []
        for d in valid:
            _, role = d.split("=", 1)
            role = role.strip()
            if role and role not in roles:
                roles.append(role)
        return roles

    def _gen_note_rows(self):
        """Create N comparison rows with dropdown menus populated from the Dyes."""
        # Save existing selections + titles
        saved = []
        for ca, cb, ent in self._note_rows:
            saved.append((ca.get(), cb.get(), ent.get().strip()))

        # Clear existing rows
        if hasattr(self, "_note_placeholder") and self._note_placeholder.winfo_exists():
            self._note_placeholder.destroy()
        for ca, cb, ent in self._note_rows:
            ca.destroy()
            cb.destroy()
            # also destroy the "in" label in between
            ent.destroy()
        # destroy any leftover "in" labels from previous generation
        for w in self.notes_frame.grid_slaves():
            if int(w.grid_info()["row"]) >= 1:
                w.destroy()
        self._note_rows.clear()

        roles = self._get_roles()
        n = self._n_comparisons.get()
        if n <= 0:
            self._note_placeholder = tk.Label(
                self.notes_frame, text="   Set comparisons to 1 or more and click 'Generate Rows'.",
                bg="white", fg="grey", anchor="w")
            self._note_placeholder.grid(row=1, column=0, columnspan=4, sticky="w", padx=5, pady=8)
            return
        if not roles:
            self._note_placeholder = tk.Label(
                self.notes_frame, text="   Add at least 1 dye with a role in the Dyes box above.",
                bg="white", fg="grey", anchor="w")
            self._note_placeholder.grid(row=1, column=0, columnspan=4, sticky="w", padx=5, pady=8)
            return

        # Ensure 'Cell' is always available as an option
        cb_roles = list(roles)
        if "Cell" not in cb_roles:
            cb_roles.append("Cell")

        from itertools import permutations
        perms = list(permutations(cb_roles, 2))

        for i in range(n):
            ca = ttk.Combobox(self.notes_frame, values=cb_roles, width=12, state="readonly")
            lbl_in = tk.Label(self.notes_frame, text=" in ", bg="white")
            cb = ttk.Combobox(self.notes_frame, values=cb_roles, width=12, state="readonly")
            ent = tk.Entry(self.notes_frame, width=30)

            ca.grid(row=i + 1, column=0, padx=5, pady=2, sticky="w")
            lbl_in.grid(row=i + 1, column=1, pady=2)
            cb.grid(row=i + 1, column=2, padx=5, pady=2, sticky="w")
            ent.grid(row=i + 1, column=3, padx=5, pady=2, sticky="we")

            # Restore previous selections, otherwise auto-fill from permutations
            if i < len(saved) and saved[i][0] and saved[i][1]:
                sa, sb, st = saved[i]
                if sa in cb_roles:
                    ca.set(sa)
                if sb in cb_roles:
                    cb.set(sb)
                if st:
                    ent.insert(0, st)
            elif i < len(perms):
                ca.set(perms[i][0])
                cb.set(perms[i][1])

            self._note_rows.append((ca, cb, ent))

    def _stopped(self, title, stop):
        """Show a deliberate stop from the analysis as a message.

        The analysis says "stop, here is why" by raising SystemExit - no cells
        found, no intensity column, a spreadsheet left open in Excel. That is
        not an Exception, so every `except Exception` in this file let it
        through Tk, and the app closed instantly with nothing written
        anywhere. The reason is usually something the scientist can fix in a
        few seconds, so it has to reach them.
        """
        from tkinter import messagebox
        msg = str(stop) or "The analysis stopped without giving a reason."
        self._write(f"\n[stopped] {msg}\n")
        messagebox.showerror(title, msg)

    def _reuse_window(self, attr):
        """Focus an already-open tool window, if there is one."""
        w = getattr(self, attr, None)
        try:
            if w is not None and w.winfo_exists():
                w.deiconify()
                w.lift()
                w.focus_force()
                return True
        except Exception:
            pass
        return False

    def _use_recent(self, _evt=None):
        """Switch to a dataset used before, and say what state it is in.

        Index 0 is the blank entry: choosing it clears the paths rather than
        leaving the box stuck on whatever was picked first.
        """
        # A run in progress owns the paths; changing them now would leave the
        # window describing one dataset while another is being measured.
        if any(str(b.cget("state")) == "disabled" for b in self.buttons):
            self._write("A run is in progress - the folder cannot be changed "
                        "until it finishes.\n")
            return
        i = self.recent_box.current()
        if i == 0:
            self.folder.delete(0, "end")
            self.outdir.delete(0, "end")
            self._user_picked_outdir = False
            self._write("Cleared. Choose a folder, or pick another recent project.\n")
            return
        i -= 1
        if i < 0 or i >= len(self._recent):
            return
        p = self._recent[i]
        self.folder.delete(0, "end")
        self.folder.insert(0, p["folder"])
        if p.get("outdir"):
            self.outdir.delete(0, "end")
            self.outdir.insert(0, p["outdir"])
            self._user_picked_outdir = True
        else:
            self._user_picked_outdir = False
            self._update_outdir()
        try:
            from cell_viability import session as _ss
            self._write("\nOpened " + p["folder"] + "\n  status: "
                        + _ss.project_progress(p["folder"]) + "\n")
        except Exception:
            pass

    @staticmethod
    def _start_dir(*candidates):
        """The first of these that exists and can be listed.

        Neither picker said where to start, so Windows reused whichever
        folder was touched last - which meant going to choose your IMAGES
        opened in the folder you had just chosen for RESULTS. A path on a
        server that is not mounted is skipped rather than opened into, since
        the dialog stalls on those.
        """
        for c in candidates:
            c = (c or "").strip().strip('"')
            if not c:
                continue
            try:
                if os.path.isdir(c) and os.access(c, os.R_OK):
                    return c
            except Exception:
                continue
            parent = os.path.dirname(c.rstrip("\\/"))
            try:
                if parent and os.path.isdir(parent) and os.access(parent, os.R_OK):
                    return parent
            except Exception:
                continue
        return ""

    def pick(self):
        # start where the images are, or beside them - never in the results
        start = self._start_dir(self.folder.get(),
                                os.path.join(os.path.expanduser("~"), "Desktop"))
        d = filedialog.askdirectory(
            title="Pick the folder with your .czi images",
            initialdir=start or None)
        if d:
            self.folder.delete(0, "end"); self.folder.insert(0, d)
            self._update_outdir()

    def pick_outdir(self):
        # start where the results already go; failing that, somewhere local,
        # because results should not default onto the microscope's server
        start = self._start_dir(self.outdir.get(),
                                os.path.join(os.path.expanduser("~"), "Desktop"))
        d = filedialog.askdirectory(title="Pick where to save results",
                                    initialdir=start or None)
        if d:
            self.outdir.delete(0, "end"); self.outdir.insert(0, d)
            self._user_picked_outdir = True

    def _update_outdir(self, event=None):
        if getattr(self, "_user_picked_outdir", False):
            return
        src = self.folder.get().strip().strip('"')
        if not src:
            return
        import datetime
        import os
        date_str = datetime.datetime.now().strftime("%Y-%m-%d")
        title = self.title.get().strip()
        folder_name = f"{date_str} - {title}" if title else date_str
        self.outdir.delete(0, "end")
        self.outdir.insert(0, os.path.join(src, folder_name))

    def _write(self, s):
        self.log.configure(state="normal")
        self.log.insert("end", s); self.log.see("end")
        self.log.configure(state="disabled")

    def _apply(self):
        """Push the fields into coloc.py's config (it stays the engine)."""
        raw_dyes = self.dyes.get("1.0", "end").splitlines()
        coloc.DYES = [d for d in raw_dyes if d.strip() and d.strip() not in self.dye_examples]
        coloc._DYES = coloc._parse_map(coloc.DYES)
        
        raw_cols = self.colours.get("1.0", "end").splitlines()
        coloc.COLORS = [c for c in raw_cols if c.strip() and c.strip() not in self.col_examples]
        coloc._COLORS = coloc._parse_map(coloc.COLORS)
        
        coloc.NOTES = []
        coloc._NOTES.clear()
        # The comparison rows say which comparisons are being looked for, not
        # merely which ones get a custom title. They used to do nothing at all
        # unless a title had also been typed, which it usually had not.
        coloc.PAIRS_WANTED = []
        coloc.COMPUTE_ALL_PAIRS = bool(self._all_pairs.get())
        for ca, cb, ent in self._note_rows:
            a, b = ca.get().strip(), cb.get().strip()
            if a and b:
                coloc.PAIRS_WANTED.append((a, b))
                title = ent.get().strip()
                if title:
                    coloc._NOTES[(a, b)] = title
                    coloc.NOTES.append(f"{a} in {b} = {title}")

        coloc.THRESH_METHOD = self.thresh.get()
        coloc.SEGMENT_METHOD = self.segment.get()
        coloc.EXPERIMENT_TITLE = self.title.get().strip()

    def _set_inputs_enabled(self, on):
        """Lock or unlock the boxes that say WHICH dataset is being analysed.

        Only the buttons were disabled during a run. The Recent dropdown and
        the two path boxes stayed live, so a folder could be swapped while an
        analysis was running - and choosing one printed "Opened ..." into the
        middle of that run's log, which is how this was noticed.

        The log noise was the harmless half. The window was left describing a
        different dataset from the one being measured, so when the run
        finished and the buttons came back, "Check the cells" and "Open
        Results Folder" pointed somewhere the run had never been.
        """
        state = "normal" if on else "disabled"
        for w in (getattr(self, "folder", None), getattr(self, "outdir", None)):
            try:
                w.configure(state=state)
            except Exception:
                pass
        box = getattr(self, "recent_box", None)
        if box is not None:
            try:
                box.configure(state="readonly" if on else "disabled")
            except Exception:
                pass

    def go(self, fn):
        folder = self.folder.get().strip().strip('"')
        if not folder or not os.path.isdir(folder):
            self._write("Please choose a valid folder first.\n"); return
        outdir = self.outdir.get().strip().strip('"')

        self._apply()
        try:                       # so the app reopens here next time
            from cell_viability import session as _ss
            _ss.remember_project(folder, outdir)
        except Exception:
            pass
        for b in self.buttons:
            b.configure(state="disabled")
        self._set_inputs_enabled(False)
        self.spinner_idx = 0
        self.start_time = time.time()
        self._eta_phase = None
        self._eta_marks = []
        self._eta_prev = None
        self.total_files = None
        self.status.configure(text=f"[{' ' * 10}] Running... Calculating ETA")
        self._write(f"\n=== {fn.__name__}  on  {folder} ===\n")
        threading.Thread(target=self._worker, args=(fn, folder, outdir), daemon=True).start()
        self.root.after(120, self._poll)

    def _worker(self, fn, folder, outdir):
        class _W:
            def __init__(s, q): s.q = q
            def write(s, m): s.q.put(m)
            def flush(s): pass
        old = sys.stdout
        sys.stdout = _W(self.q)
        try:
            import inspect
            sig = inspect.signature(fn)
            if "outdir" in sig.parameters:
                fn(folder, outdir=outdir)
            else:
                fn(folder)
        except SystemExit as e:
            self.q.put(f"\n[error] {e}\n")
        except Exception as e:
            self.q.put(f"\n[error] {e}\n")
        finally:
            sys.stdout = old
            self.q.put(None)          # done sentinel

    def _what_next(self):
        """Say what was produced and what to do with it.

        Finishing a run used to leave a wall of log text and no indication of
        which file to open or what the next step was.
        """
        out = self.outdir.get().strip().strip('"')
        try:
            from cell_viability import outputs as OUT
            figs = OUT.figures_dir(out)
            if not os.path.isdir(figs):
                return
            self._write(
                "\n"
                "--------------------------------------------------------\n"
                " FINISHED. What you have:\n"
                "\n"
                "   Figures and Tables\\  the figure, and Results.xlsx with\n"
                "                        every number in it\n"
                "   Cell Images\\         the cells it measured, one folder\n"
                "                        per experiment folder\n"
                "   README.txt           what each folder holds, and the\n"
                "                        counts for this run\n"
                "\n"
                " Next, if you want to:\n"
                "   2. Check the cells    look at what it picked, fix any\n"
                "                         mistakes, rebuild\n"
                "   3. Adjust the figure  titles, colours, order\n"
                "--------------------------------------------------------\n")
        except Exception:
            pass

    def _progress(self, msg):
        """Update the bar and the estimate from one progress line.

        The old estimate was elapsed-since-the-button divided by files done.
        Three things were wrong with it.

        It counted the startup - walking the folder, reading channel metadata,
        loading the model - against the first file, so the first estimate was
        wildly high and then fell for the rest of the run.

        It averaged over every file since the start, so it could not react.
        A run that speeds up once the cache is warm kept quoting the cold rate.

        And it only knew about the measuring loop. Drawing the example images
        reloads every image again, once per comparison, and reported nothing -
        so the bar sat at full and "0m 0s left" while minutes of work went on.
        That is the one that makes a timer worth distrusting.
        """
        body = msg.strip().split("__PROGRESS__")[1]
        parts = body.split(",")
        i, total = int(parts[0]), int(parts[1])
        phase = parts[2].strip() if len(parts) > 2 else ""

        now = time.time()
        if phase != getattr(self, "_eta_phase", None):
            # A new stage runs at its own speed, so its clock starts fresh -
            # including the carried estimate. Keeping that meant the example
            # images stage opened by quoting the measuring stage's last
            # number, announcing "about 15s left" with four minutes to go.
            self._eta_phase = phase
            self._eta_marks = []
            self._eta_prev = None
        self.total_files = total

        marks = self._eta_marks
        marks.append((i, now))
        if len(marks) > 12:                 # a rolling window, so it adapts
            del marks[0]

        eta_str = "estimating..."
        if len(marks) >= 2:
            (i0, t0), (i1, t1) = marks[0], marks[-1]
            done = i1 - i0
            if done > 0 and t1 > t0:
                per = (t1 - t0) / done
                left = max(0, total - i1) * per
                # blend with the previous estimate so it settles instead of
                # jumping every time one image happens to be slow
                prev = getattr(self, "_eta_prev", None)
                if prev is not None:
                    # Weighted towards the new reading. An even blend was too
                    # sluggish: when the segmentation cache warmed up and the
                    # rate went from 8s an image to 2s, the estimate spent a
                    # third of the run still quoting the cold one.
                    left = 0.75 * left + 0.25 * prev
                self._eta_prev = left
                eta_str = self._fmt_eta(left)
        elif getattr(self, "_eta_prev", None) is not None:
            eta_str = self._fmt_eta(self._eta_prev)

        filled = int(10 * (i / total)) if total else 0
        bar = ("|" * filled) + (" " * (10 - filled))
        where = f"{phase} {min(i + 1, total)}/{total}" if phase else "Running..."
        self.status.configure(text=f"[{bar}] {where} - {eta_str}")

    @staticmethod
    def _fmt_eta(seconds):
        """Say it at a precision the estimate can actually support.

        Rounding is done on the total, not on the minutes and the seconds
        separately - taking them apart produced "6m 60s left", which is a
        way of saying seven minutes that makes a reader doubt the whole
        number, and rightly.
        """
        seconds = max(0, float(seconds))
        if seconds < 10:
            return "nearly done"
        if seconds < 90:
            return f"about {int(round(seconds / 5.0) * 5)}s left"
        total = int(round(seconds / 15.0) * 15)      # to the nearest 15s
        mins, secs = divmod(total, 60)
        if mins >= 10:
            return f"about {int(round(total / 60.0))}m left"
        return f"about {mins}m {secs:02d}s left" if secs else f"about {mins}m left"

    def _poll(self):
        try:
            while True:
                m = self.q.get_nowait()
                if m is None:
                    for b in self.buttons:
                        b.configure(state="normal")
                    self._set_inputs_enabled(True)
                    self.btn_open_results.configure(state="normal")
                    self.status.configure(text="Done.")
                    self._what_next()
                    return
                if isinstance(m, str) and "__PROGRESS__" in m:
                    try:
                        self._progress(m)
                    except Exception:
                        pass
                    continue
                self._write(m)
        except queue.Empty:
            pass
        
        # fallback spinner if no total files known yet
        if getattr(self, "total_files", None) is None:
            self.spinner_idx = (self.spinner_idx + 1) % 11
            bar = ('|' * self.spinner_idx) + (' ' * (10 - self.spinner_idx))
            self.status.configure(text=f"[{bar}] Running... Calculating time remaining...")
            
        self.root.after(120, self._poll)

    def view_colors(self):
        if hasattr(self, "color_win") and self.color_win is not None and self.color_win.winfo_exists():
            self.color_win.deiconify()
            self.color_win.lift()
            self.color_win.focus_force()
            return
            
        self.color_win = tk.Toplevel(self.root)
        top = self.color_win
        top.title("Legal Colors")
        
        # Position the popup nicely relative to the main window
        x = self.root.winfo_x() + 50
        y = self.root.winfo_y() + 50
        top.geometry(f"+{x}+{y}")
        
        txt = scrolledtext.ScrolledText(top, width=72, height=32, wrap="none", padx=20, pady=20, font=("Consolas", 10))
        txt.pack(fill="both", expand=True)
        try:
            import sys
            base_path = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
            colors_path = os.path.join(base_path, "COLORS.txt")
            if not os.path.exists(colors_path):
                colors_path = "COLORS.txt"
            with open(colors_path, "r", encoding="utf-8") as f:
                txt.insert("1.0", f.read())
        except Exception as e:
            txt.insert("1.0", f"Could not load COLORS.txt: {e}")
        txt.configure(state="disabled")

    def open_results(self):
        """Reveal the results folder in the desktop file manager.

        os.startfile exists ONLY on Windows; calling it on macOS raises
        AttributeError and the button appears to do nothing.
        """
        import subprocess
        outdir = self.outdir.get().strip().strip('"')
        if not outdir:
            outdir = self.folder.get().strip().strip('"')
        if not os.path.exists(outdir):
            self._write(f"Nothing to open yet: {outdir or '(no folder set)'}\n")
            return
        try:
            if sys.platform == "darwin":
                subprocess.Popen(["open", outdir])
            elif os.name == "nt":
                os.startfile(outdir)            # noqa: F821  (Windows only)
            else:
                subprocess.Popen(["xdg-open", outdir])
        except Exception as exc:
            self._write(f"Could not open {outdir}: {exc}\n")

    def show_dye_guide(self):
        if hasattr(self, "_guide_win") and self._guide_win.winfo_exists():
            self._guide_win.deiconify()
            self._guide_win.lift()
            self._guide_win.focus_force()
            return
            
        top = tk.Toplevel(self.root)
        self._guide_win = top
        top.title("Common Dyes Reference")
        x = self.root.winfo_x() + 50
        y = self.root.winfo_y() + 50
        top.geometry(f"820x450+{x}+{y}")
        top.configure(bg=BG)
        
        lbl = tk.Label(top, text="Dye Reference Guide", font=("Segoe UI", 11, "bold"), bg=BG)
        lbl.pack(anchor="w", padx=10, pady=(10, 5))
        
        txt = scrolledtext.ScrolledText(top, width=85, height=20, wrap="none", padx=20, pady=20, font=("Consolas", 10))
        txt.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        
        lines = ["If you don't know the exact name, typing a partial name "
                 "(like 'alexa' or 'cy3') usually works.",
                 "",
                 "NOTE: LysoTracker marks LYSOSOMES and MitoTracker marks "
                 "MITOCHONDRIA. They are",
                 "      different organelles and the names differ by four "
                 "letters, so check which",
                 "      one your images actually carry.",
                 ""]
        lines.append(f"{'Dye Name':<24} | {'Color':<10} | {'Typical Use / Description'}")
        lines.append("-" * 90)
        for name, desc in coloc.DYE_GUIDE.items():
            lines.append(f"{name:<24} | {desc[1]:<10} | {desc[0]}")
            
        txt.insert("1.0", "\n".join(lines))
        txt.configure(state="disabled")

    def show_role_guide(self):
        if hasattr(self, "_role_win") and self._role_win.winfo_exists():
            self._role_win.deiconify()
            self._role_win.lift()
            self._role_win.focus_force()
            return
            
        top = tk.Toplevel(self.root)
        self._role_win = top
        top.title("Common Biological Roles")
        x = self.root.winfo_x() + 50
        y = self.root.winfo_y() + 50
        top.geometry(f"600x400+{x}+{y}")
        top.configure(bg="#f4f6f9")
        
        lbl = tk.Label(top, text="Suggested Roles", font=("Segoe UI", 11, "bold"), bg="#f4f6f9")
        lbl.pack(anchor="w", padx=10, pady=(10, 5))
        
        txt = scrolledtext.ScrolledText(top, width=60, height=20, wrap="none", padx=20, pady=20, font=("Consolas", 10))
        txt.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        
        lines = [
            "Use these consistent names for 'roles' in your configurations.",
            "Consistently naming roles helps group data correctly in the output.\n",
            "  cell         - Entire cellular area / cytoplasm",
            "  nucleus      - The cell nucleus (e.g. DAPI/Hoechst)",
            "  vesicle      - General vesicles",
            "  lysosome     - Lysosomes",
            "  endosome     - Endosomes",
            "  membrane     - Plasma membrane",
            "  mitochondria - Mitochondria",
            "  bacteria     - Bacterial cells",
            "  lipid        - Lipid droplets",
            "  protein      - Unspecified protein clusters",
            "  control      - Use 'control' to exclude from some analyses",
        ]
            
        txt.insert("1.0", "\n".join(lines))
        txt.configure(state="disabled")

    def popup_coloc(self):
        if hasattr(self, "_coloc_win") and self._coloc_win is not None and self._coloc_win.winfo_exists():
            self._coloc_win.deiconify()
            self._coloc_win.lift()
            self._coloc_win.focus_force()
            return

        top = tk.Toplevel(self.root)
        self._coloc_win = top
        top.title("Run Colocalization")
        x = self.root.winfo_x() + 50
        y = self.root.winfo_y() + 50
        top.geometry(f"+{x}+{y}")
        top.protocol("WM_DELETE_WINDOW", top.withdraw)
        
        self._var_excel = tk.BooleanVar(value=getattr(coloc, "SAVE_EXCEL", True))
        self._var_plots = tk.BooleanVar(value=getattr(coloc, "SAVE_PLOTS", True))
        self._var_panels = tk.BooleanVar(value=getattr(coloc, "SAVE_PANELS", True))
        self._var_3d = tk.BooleanVar(value=getattr(coloc, "USE_3D_VOLUMETRIC", False))
        self._var_each = tk.BooleanVar(value=getattr(coloc, "SAVE_EACH_IMAGE",
                                                    False))

        tk.Checkbutton(top, text="Generate Excel Data Matrix", variable=self._var_excel).pack(anchor="w", padx=20, pady=(20, 5))
        tk.Checkbutton(top, text="Generate Summary Bar Charts (Endosomal Escape)", variable=self._var_plots).pack(anchor="w", padx=20, pady=5)
        tk.Checkbutton(top, text="Generate Representative Panel Figures (Images & Scatterplots)", variable=self._var_panels).pack(anchor="w", padx=20, pady=5)

        tk.Checkbutton(
            top, variable=self._var_each, justify="left",
            text=("Also save EACH IMAGE on its own figure\n"
                  "(one file per image - roughly triples the run)")
            ).pack(anchor="w", padx=20, pady=5)

        tk.Frame(top, height=1, bg="grey").pack(fill="x", padx=20, pady=10)
        tk.Checkbutton(
            top, variable=self._all_pairs,
            text=("Measure EVERY comparison, not only the rows in the window\n"
                  "(three dyes = six comparisons; each reloads every image)"),
            justify="left").pack(anchor="w", padx=20, pady=5)
        
        tk.Frame(top, height=1, bg="grey").pack(fill="x", padx=20, pady=10)
        tk.Checkbutton(top, text="True 3D Volumetric Analysis (disable Z-stack flattening, HIGH MEMORY)", 
                       variable=self._var_3d, fg="red").pack(anchor="w", padx=20, pady=5)
        
        def _execute():
            folder = self.folder.get().strip().strip('"')
            if not folder or not os.path.isdir(folder):
                self._write("Choose your images folder first.\n")
                return
            if not self._confirm_coloc(folder, "whole"):
                return
            coloc.SAVE_EXCEL = self._var_excel.get()
            coloc.SAVE_PLOTS = self._var_plots.get()
            coloc.SAVE_PANELS = self._var_panels.get()
            coloc.SAVE_EACH_IMAGE = self._var_each.get()
            coloc.USE_3D_VOLUMETRIC = self._var_3d.get()
            top.withdraw()
            self.go(coloc.run)
            
        tk.Button(top, text="RUN", command=_execute, bg="#d4edda", width=15).pack(pady=20)

    def open_review_app(self):
        """Check and correct which cells the picker selected, then re-run."""
        from tkinter import messagebox

        folder = self.folder.get().strip().strip('"')
        if not folder or not os.path.isdir(folder):
            self._write("Choose your images folder first.\n")
            return
        # Ask where the working folder actually is. Hard-coding it next to the
        # images stopped being right when it moved out of them.
        from cell_viability import livecell as _lc
        cand = os.path.join(_lc.workspace(folder, create=False),
                            "all_candidates.csv")
        if not os.path.isfile(cand):
            messagebox.showinfo(
                "Nothing to review yet",
                "Run 'Brightest Live Cells' once first.\n\n"
                "That produces the cell selection; this window is where you "
                "correct it.")
            return
        self._write(f"\n--- Review cells: {folder} ---\n"
                    "Click a cell to keep/reject it, press D to draw a missed "
                    "one, R to re-run with your corrections.\n")

        # On the MAIN thread, as a child of this window. Tkinter allows only
        # one Tk() root per process and is not thread-safe; a second root
        # driven from a worker thread fails with a missing-image error.
        if self._reuse_window("_review_win"):
            return
        try:
            from cell_viability.review_app import run as run_review
            self._review_win = run_review(
                folder, master=self.root,
                outdir=(self.outdir.get().strip().strip('"') or None))
        except SystemExit as stop:
            self._stopped("Cannot open the review window", stop)
        except Exception as exc:
            self._write('[error] review: ' + str(exc))

    def _dye_survey(self, folder):
        """(roles, folders that can be measured, folders that cannot).

        Reads one image from EVERY folder. Reading only the first one said
        "these images have 1 dye" about a dataset whose first nine folders
        carry Cy3 alone and whose last six carry three dyes - a statement
        that was false about the dataset and unhelpful about the folder.
        """
        try:
            per, usable, skipped = coloc.survey_dyes(folder)
        except Exception:
            return [], [], []
        roles = []
        for name in usable:
            for r in per.get(name, []):
                if r not in roles:
                    roles.append(r)
        return roles, usable, skipped

    def _skipped_note(self, skipped, per_folder_count):
        if not skipped:
            return ""
        head = (f"\n{len(skipped)} folder(s) have fewer than two dyes and "
                f"will be skipped:")
        shown = "\n".join("    " + str(n) for n in skipped[:6])
        more = (f"\n    ... and {len(skipped) - 6} more"
                if len(skipped) > 6 else "")
        return head + "\n" + shown + more + "\n"

    def _confirm_coloc(self, folder, kind):
        """Say what will be measured, and how much, before doing any of it.

        The buttons went straight to work. With three dyes that is six
        comparisons and six passes over the images to answer one question,
        with nothing said about which comparisons those were or how long it
        would take - and for the in-cells run, nothing about which cells it
        was going to use, which made "in cells" mean whichever cells some
        earlier run happened to leave behind.

        Returns True to go ahead, False to stop.
        """
        from tkinter import messagebox
        self._apply()

        # Every folder, not just the first. Folders in one dataset are not
        # always stained the same way, and reading only the first said "these
        # images have 1 dye" about a dataset whose later folders carry three.
        names, usable, skipped = self._dye_survey(folder)
        n_dye = len(names)
        if not usable:
            messagebox.showinfo(
                "Not enough dyes",
                "Colocalization compares two dyes, and no folder here has "
                "two.\n\nEvery folder was checked; each has one fluorescence "
                "channel or none.")
            return False

        total = n_dye * (n_dye - 1)          # directional pairs
        asked = len(coloc.PAIRS_WANTED)
        every = bool(coloc.COMPUTE_ALL_PAIRS)
        lines = [f"Dyes found: {', '.join(names)}",
                 f"Folders to measure: {len(usable)}"]
        _note = self._skipped_note(skipped, len(usable))
        if _note:
            lines.append(_note)
        lines.append("")

        if asked and not every:
            lines += [f"You asked for {asked} comparison(s):"]
            lines += [f"    {a} in {b}" for a, b in coloc.PAIRS_WANTED]
            lines += ["",
                      f"Those will be measured. The other {max(0, total - asked)} "
                      f"permutation(s) will be SKIPPED.",
                      "",
                      "Do them all instead? Tick 'Also compute every other "
                      "comparison'",
                      "in the window and press this button again."]
        else:
            lines += [f"ALL {total} permutations will be measured.",
                      "",
                      "Each one reloads every image to draw its example "
                      "pictures,",
                      "so this is roughly " + str(total) + "x the work of a "
                      "single comparison.",
                      "",
                      "To do fewer: fill in the comparison rows and untick",
                      "'Also compute every other comparison'."]

        if kind == "cells":
            n_cells, n_img, n_fold = self._selection_size(folder)
            if not n_cells:
                messagebox.showinfo(
                    "Find the cells first",
                    "This measures colocalization INSIDE the cells, so the "
                    "cells have to be found first.\n\n"
                    "Press '1. Find and measure cells', then this button "
                    "again.\n\n"
                    "(If you do not want cell-by-cell numbers, "
                    "'Colocalization (whole image)' needs nothing "
                    "beforehand.)")
                return False
            lines = ([f"Measuring inside {n_cells} cells "
                      f"({n_img} images, {n_fold} folders)",
                      "as chosen by the last cell run, including your "
                      "corrections.", ""] + lines)

        return messagebox.askyesno(
            "Run colocalization?" if kind == "whole"
            else "Run colocalization in cells?",
            "\n".join(lines) + "\n\nGo ahead?")

    def _selection_size(self, folder):
        """How many cells the last cell run left, and where they came from."""
        try:
            import pandas as pd
            from cell_viability import livecell as _lc
            p = os.path.join(_lc.workspace(folder, create=False),
                             "all_candidates.csv")
            if not os.path.isfile(p):
                return 0, 0, 0
            d = pd.read_csv(p)
            d = d[d["selected"] == 1]
            return (len(d), int(d["file"].nunique()),
                    int(d["condition"].nunique()))
        except Exception:
            return 0, 0, 0

    def run_coloc_live(self):
        """Colocalization measured inside each selected live cell."""
        from tkinter import messagebox
        folder = self.folder.get().strip().strip('"')
        if not folder or not os.path.isdir(folder):
            self._write("Choose your images folder first.\n")
            return
        n_cells, n_img, n_fold = self._selection_size(folder)
        if not n_cells:
            messagebox.showinfo(
                "Find the cells first",
                "This measures colocalization INSIDE the cells, so the cells "
                "have to be found first.\n\n"
                "Press '1. Find and measure cells', then this button "
                "again.\n\n"
                "(If you do not want cell-by-cell numbers, "
                "'Colocalization (whole image)' needs nothing beforehand.)")
            return

        self._apply()
        names, usable, skipped = self._dye_survey(folder)
        if not usable:
            messagebox.showinfo(
                "Not enough dyes",
                "Colocalization compares two dyes, and no folder here has "
                "two.\n\nEvery folder was checked; each has one "
                "fluorescence channel or none.")
            return

        if self._reuse_window("_coloc_cells_win"):
            return
        top = tk.Toplevel(self.root)
        self._coloc_cells_win = top
        top.title("Run Colocalization in cells")
        top.geometry(f"+{self.root.winfo_x() + 60}+{self.root.winfo_y() + 60}")
        top.protocol("WM_DELETE_WINDOW", top.withdraw)

        # What it is about to do. "In cells" used to mean whichever cells some
        # earlier run had left behind, with no way to know what or how many
        # without going and looking in the results folder.
        tk.Label(top, justify="left", anchor="w", font=("Segoe UI", 10),
                 text=(f"Measuring inside {n_cells} cells\n"
                       f"    from {n_img} image(s) in {n_fold} folder(s)\n"
                       f"    as chosen by the last cell run, including your "
                       f"corrections.\n\n"
                       f"Dyes found: {', '.join(names)}\n"
                       f"Folders with two or more dyes: {len(usable)}"
                       + self._skipped_note(skipped, len(usable)))
                 ).pack(anchor="w", padx=20, pady=(20, 10))

        tk.Frame(top, height=1, bg="grey").pack(fill="x", padx=20, pady=6)
        tk.Checkbutton(
            top, variable=self._all_pairs, justify="left",
            text=("Measure EVERY comparison, not only the rows in the "
                  "window\n"
                  f"({len(names)} dyes = {len(names) * (len(names) - 1)} "
                  f"comparisons)")).pack(anchor="w", padx=20, pady=5)

        def _execute():
            top.withdraw()
            self._apply()

            def colocalization_in_live_cells(folder, outdir=None):
                from cell_viability import coloc_live
                return coloc_live.run(folder, outdir=outdir)

            self.go(colocalization_in_live_cells)

        tk.Button(top, text="RUN", command=_execute, bg="#d4edda",
                  width=15).pack(pady=20)

    def open_figure_editor(self):
        """Adjust the finished figure - names, order, colours, error bars,
        excluded images - without re-running the analysis."""
        from tkinter import filedialog, messagebox

        start = (self.outdir.get().strip().strip('"')
                 or self.folder.get().strip().strip('"') or ".")
        try:
            from cell_viability import report as RP
            RP._find_live_csv(start)
            target = start
        except SystemExit:
            target = filedialog.askdirectory(
                title="Pick the results folder (the one containing _summary)",
                initialdir=start if os.path.isdir(start) else ".")
            if not target:
                return
            try:
                from cell_viability import report as RP2
                RP2._find_live_csv(target)
            except SystemExit as exc:
                messagebox.showerror("No results there", str(exc))
                return

        self._write(f"\n--- Figure editor on {target} ---\n")

        # On the MAIN thread, as a child of this window. Tkinter allows only
        # one Tk() root per process and is not thread-safe; a second root
        # driven from a worker thread fails with a missing-image error.
        if self._reuse_window("_editor_win"):
            return
        try:
            from cell_viability.figure_editor import run as run_editor
            self._editor_win = run_editor(target, master=self.root)
        # SystemExit is not an Exception, so it went straight through Tk and
        # took the whole app down without a word. It is how the analysis says
        # "stop, here is why", and the scientist needs to read the why.
        except SystemExit as stop:
            self._stopped("Cannot open the figure editor", stop)
        except Exception as exc:
            self._write('[error] figure editor: ' + str(exc))

    def popup_strongest(self):
        """Find the cells, measure them, rank the brightest N in each folder.

        One question is shown. Everything else has a default that is right
        nearly always, and sits behind "More options".
        """
        if getattr(self, "_strongest_win", None) is not None and self._strongest_win.winfo_exists():
            self._strongest_win.deiconify()
            self._strongest_win.lift()
            self._strongest_win.focus_force()
            return

        from cell_viability import rank_top

        top = tk.Toplevel(self.root)
        self._strongest_win = top
        top.title("Find and measure cells")
        top.geometry(f"+{self.root.winfo_x() + 60}+{self.root.winfo_y() + 60}")
        top.protocol("WM_DELETE_WINDOW", top.withdraw)

        frame = tk.Frame(top)
        frame.pack(padx=24, pady=18, fill="both", expand=True)

        tk.Label(frame, text="Find and measure cells",
                 font=("Segoe UI", 12, "bold")).pack(anchor="w")
        tk.Label(frame, justify="left", fg="#444",
                 text=("Cells are found from the brightfield image, the dying, "
                       "overlapping\nand cut-off ones are dropped, and the rest "
                       "are measured and ranked.")
                 ).pack(anchor="w", pady=(2, 12))

        row = tk.Frame(frame)
        row.pack(anchor="w")
        tk.Label(row, text="Cells to show from each folder:",
                 font=("Segoe UI", 10, "bold")).pack(side="left")
        self._ent_n_live = tk.Entry(row, width=6, font=("Segoe UI", 11))
        self._ent_n_live.insert(0, str(getattr(rank_top, "TOP_N", 20)))
        self._ent_n_live.pack(side="left", padx=8)
        tk.Label(frame, text="Every folder is treated as its own experiment.",
                 fg="grey", font=("Segoe UI", 8, "italic")).pack(anchor="w")

        # ---------------- more options (hidden by default) ----------------
        adv = tk.Frame(frame)
        self._adv_shown = tk.BooleanVar(value=False)

        def _toggle_adv():
            if self._adv_shown.get():
                adv.pack(anchor="w", fill="x", pady=(10, 0))
            else:
                adv.pack_forget()
            top.geometry("")

        tk.Checkbutton(frame, text="More options", variable=self._adv_shown,
                       command=_toggle_adv, fg="#333").pack(anchor="w",
                                                            pady=(12, 0))

        def opt_row(parent, label):
            fr = tk.Frame(parent)
            fr.pack(anchor="w", fill="x", pady=3)
            tk.Label(fr, text=label, width=22, anchor="w").pack(side="left")
            return fr

        fr = opt_row(adv, "Rank by:")
        self._var_metric = tk.StringVar(
            value=("Total dye in cell"
                   if getattr(rank_top, "METRIC", "cy3_integrated") == "cy3_integrated"
                   else "Brightness per area"))
        ttk.Combobox(fr, textvariable=self._var_metric, width=20, state="readonly",
                     values=["Total dye in cell", "Brightness per area"]
                     ).pack(side="left")

        fr = opt_row(adv, "Measure:")
        self._var_target = tk.StringVar(
            value=("Dead cells" if getattr(rank_top, "TARGET", "live") == "dead"
                   else "Live cells"))
        ttk.Combobox(fr, textvariable=self._var_target, width=20, state="readonly",
                     values=["Live cells", "Dead cells"]).pack(side="left")

        fr = opt_row(adv, "Cells in the dish:")
        self._var_layout = tk.StringVar(
            value={"auto": "Work it out for me", "sparse": "Separated cells",
                   "confluent": "Confluent sheet"}.get(
                       getattr(rank_top, "LAYOUT", "auto"), "Work it out for me"))
        ttk.Combobox(fr, textvariable=self._var_layout, width=20, state="readonly",
                     values=["Work it out for me", "Separated cells",
                             "Confluent sheet"]).pack(side="left")

        self._var_equal = tk.BooleanVar(
            value=getattr(rank_top, "EQUALISE_DEPTH", False))
        tk.Checkbutton(adv, text="Compare folders at equal depth",
                       variable=self._var_equal).pack(anchor="w", pady=(6, 0))
        tk.Label(adv, justify="left", fg="grey", font=("Segoe UI", 8, "italic"),
                 text=("Folders usually yield different numbers of usable cells,\n"
                       "so N reaches a different depth in each. This trims every\n"
                       "folder to the smallest first.")).pack(anchor="w")

        self._var_overlays = tk.BooleanVar(value=True)
        tk.Checkbutton(adv, text="Save whole images showing what was kept",
                       variable=self._var_overlays).pack(anchor="w", pady=(6, 0))

        tk.Label(frame, justify="left", fg="grey", font=("Segoe UI", 8, "italic"),
                 text=("The first run looks at every image once and remembers it, "
                       "so it is\nslow the first time and quick afterwards.")
                 ).pack(anchor="w", pady=(12, 0))

        def _execute():
            try:
                rank_top.TOP_N = max(1, int(self._ent_n_live.get().strip()))
            except ValueError:
                rank_top.TOP_N = 20
            rank_top.METRIC = ("cy3_integrated"
                               if self._var_metric.get().startswith("Total")
                               else "cy3_mean")
            rank_top.TARGET = ("dead" if self._var_target.get() == "Dead cells"
                               else "live")
            rank_top.LAYOUT = {"Separated cells": "sparse",
                               "Confluent sheet": "confluent"}.get(
                                   self._var_layout.get(), "auto")
            rank_top.EQUALISE_DEPTH = bool(self._var_equal.get())
            make_ov = self._var_overlays.get()
            top.withdraw()

            def find_and_measure_cells(folder, outdir=None):
                return rank_top.rank_top(folder, outdir=outdir,
                                         top_n=rank_top.TOP_N,
                                         metric=rank_top.METRIC,
                                         make_overlays=make_ov)

            self.go(find_and_measure_cells)

        tk.Button(frame, text="START", command=_execute, bg="#d4edda",
                  font=("Segoe UI", 11, "bold"), height=2).pack(fill="x",
                                                                pady=(16, 0))


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
