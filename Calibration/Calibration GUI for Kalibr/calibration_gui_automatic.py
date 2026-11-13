#!/usr/bin/env python3
"""
Automatic calibration GUI: give it the folder with the 4 full camera videos and it
extracts, in Kalibr format, only the synchronized frames where the AprilGrid is
visible in ALL 4 cameras (one every N frames inside continuous runs).

Usage:
    python calibration_gui_automatic.py
"""
from __future__ import annotations

import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import main_automatic as auto  # noqa: E402

DEFAULTS = {"interval": 10, "min_tags": 8, "scan_step": 5, "margin_px": 10, "mode": "raw"}
HINT = "#555555"


class AutoCalibGUI(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Calibration GUI - Automatic (4-camera covisible frames)")
        self.geometry("820x720")
        self.minsize(760, 620)

        self.msg_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.cancel_event = threading.Event()
        self.worker: threading.Thread | None = None

        self._build_ui()
        self.after(100, self._poll_queue)

    # ---------------- UI ----------------
    def _build_ui(self) -> None:
        pad = {"padx": 10, "pady": 6}
        frm = ttk.Frame(self)
        frm.pack(fill=tk.BOTH, expand=True)

        # Dataset
        ds = ttk.LabelFrame(frm, text="Dataset (folder with the 4 videos)")
        ds.pack(fill=tk.X, **pad)
        row = ttk.Frame(ds)
        row.pack(fill=tk.X, padx=4, pady=(8, 0))
        self.dataset_var = tk.StringVar()
        self.dataset_var.trace_add("write", lambda *_: self._refresh_videos())
        self.entry_path = ttk.Entry(row, textvariable=self.dataset_var)
        self.entry_path.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        self.browse_btn = ttk.Button(row, text="Browse…", command=self._browse)
        self.browse_btn.pack(side=tk.LEFT, padx=(0, 6))
        self.videos_lbl = ttk.Label(ds, text="Filenames must contain: aux_right, aux_left, bottom_most, stereo_bottom.",
                                    foreground=HINT, justify=tk.LEFT, font=("Consolas", 9))
        self.videos_lbl.pack(fill=tk.X, padx=12, pady=(6, 8))

        # Selection parameters
        sel = ttk.LabelFrame(frm, text="Frame selection")
        sel.pack(fill=tk.X, **pad)
        self.interval_var = tk.IntVar(value=DEFAULTS["interval"])
        self.min_tags_var = tk.IntVar(value=DEFAULTS["min_tags"])
        self.scan_step_var = tk.IntVar(value=DEFAULTS["scan_step"])
        self.margin_var = tk.IntVar(value=DEFAULTS["margin_px"])
        self.full_var = tk.BooleanVar(value=True)
        self.reuse_var = tk.BooleanVar(value=True)

        def spin_row(parent, r, label, var, lo, hi, hint):
            ttk.Label(parent, text=label).grid(row=r, column=0, sticky=tk.W, padx=(10, 6), pady=4)
            sb = ttk.Spinbox(parent, from_=lo, to=hi, textvariable=var, width=7)
            sb.grid(row=r, column=1, sticky=tk.W, pady=4)
            ttk.Label(parent, text=hint, foreground=HINT, wraplength=540, justify=tk.LEFT).grid(
                row=r, column=2, sticky=tk.W, padx=(10, 10), pady=4)
            return sb

        # --- main options (always visible) ---
        self.interval_sb = spin_row(sel, 0, "Interval (N):", self.interval_var, 1, 9999,
                                    "Minimum spacing between saved frames: inside continuous runs where the "
                                    "4 cameras see the board, keep one frame every N frames.")
        self.full_chk = ttk.Checkbutton(
            sel, text="Require the FULL 6x6 board inside the image in all 4 cameras (no cut boards)",
            variable=self.full_var, command=self._update_margin_state)
        self.full_chk.grid(row=1, column=0, columnspan=3, sticky=tk.W, padx=10, pady=(4, 2))
        self.reuse_chk = ttk.Checkbutton(
            sel, text="Reuse previous detection scan if available (fast re-run: ~2 min instead of ~8)",
            variable=self.reuse_var)
        self.reuse_chk.grid(row=2, column=0, columnspan=3, sticky=tk.W, padx=10, pady=(2, 8))

        # --- advanced options (collapsed by default) ---
        self.adv_btn = ttk.Button(frm, text="▸ Advanced", command=self._toggle_advanced, width=14)
        self.adv_btn.pack(anchor=tk.W, padx=10, pady=(0, 0))
        self.adv = ttk.LabelFrame(frm, text="Advanced (defaults are fine in most cases)")
        self.min_tags_sb = spin_row(
            self.adv, 0, "Min tags per camera:", self.min_tags_var, 4, auto.NUM_TAGS,
            f"Min AprilGrid tags (of {auto.NUM_TAGS}) every camera must detect. Ensures enough tags to "
            "reliably estimate where the whole board lies.")
        self.margin_sb = spin_row(
            self.adv, 1, "Border margin (px):", self.margin_var, 0, 500,
            "Safety gap between the full board contour and the image edge (only with FULL board).")
        self.scan_step_sb = spin_row(
            self.adv, 2, "Scan step:", self.scan_step_var, 1, 100,
            "Check the board every K frames. Only checked frames can be saved. 1 = finds everything but "
            "~5x slower. Should divide the interval.")
        self.spinboxes = [self.interval_sb, self.min_tags_sb, self.scan_step_sb]
        self._adv_shown = False

        # Filtering
        flt = ttk.LabelFrame(frm, text="Filtering (applied to saved images; detection always uses raw frames)")
        flt.pack(fill=tk.X, **pad)
        self.flt_frame = flt
        self.mode_var = tk.StringVar(value=DEFAULTS["mode"])
        self.mode_rbs = []
        for c, (val, txt) in enumerate([("raw", "Raw (recommended for Kalibr)"), ("turbid", "Turbid"), ("clear", "Clear")]):
            rb = ttk.Radiobutton(flt, text=txt, value=val, variable=self.mode_var)
            rb.grid(row=0, column=c, sticky=tk.W, padx=10, pady=8)
            self.mode_rbs.append(rb)

        # Buttons + progress
        bf = ttk.Frame(frm)
        bf.pack(fill=tk.X, **pad)
        self.start_btn = ttk.Button(bf, text="Start", command=self._on_start)
        self.start_btn.pack(side=tk.LEFT)
        self.cancel_btn = ttk.Button(bf, text="Cancel", command=self._on_cancel, state=tk.DISABLED)
        self.cancel_btn.pack(side=tk.LEFT, padx=(10, 0))
        self.open_btn = ttk.Button(bf, text="Open output folder", command=self._open_output)
        self.open_btn.pack(side=tk.LEFT, padx=(10, 0))
        self.stage_lbl = ttk.Label(bf, text="", foreground=HINT, width=22)
        self.stage_lbl.pack(side=tk.RIGHT)
        self.pbar = ttk.Progressbar(bf, mode="determinate", maximum=100, length=220)
        self.pbar.pack(side=tk.RIGHT, padx=(0, 8))

        ttk.Label(frm, text="Output: <dataset>/processed_frames/cam{0..3}_<name>/0000000100000000.jpg … "
                            "(Kalibr format, same index = same instant in the 4 cameras) + detections.csv",
                  foreground=HINT, wraplength=780, justify=tk.LEFT).pack(fill=tk.X, padx=12)

        # Log
        lf = ttk.LabelFrame(frm, text="Log")
        lf.pack(fill=tk.BOTH, expand=True, **pad)
        self.txt = tk.Text(lf, height=14, wrap=tk.WORD, state=tk.DISABLED, font=("Consolas", 9))
        sb = ttk.Scrollbar(lf, command=self.txt.yview)
        self.txt.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y, pady=10)
        self.txt.pack(fill=tk.BOTH, expand=True, padx=(10, 0), pady=10)

    # ---------------- helpers ----------------
    def _toggle_advanced(self) -> None:
        if self._adv_shown:
            self.adv.pack_forget()
            self.adv_btn.configure(text="▸ Advanced")
        else:
            self.adv.pack(fill=tk.X, padx=10, pady=6, before=self.flt_frame)
            self.adv_btn.configure(text="▾ Advanced")
        self._adv_shown = not self._adv_shown

    def _update_margin_state(self) -> None:
        self.margin_sb.configure(state=tk.NORMAL if self.full_var.get() else tk.DISABLED)

    def _browse(self) -> None:
        path = filedialog.askdirectory(title="Select folder with the 4 camera videos")
        if path:
            self.dataset_var.set(path)

    def _refresh_videos(self) -> None:
        path = self.dataset_var.get().strip()
        if not os.path.isdir(path):
            return
        vids = auto.map_videos(path)
        lines = []
        for cam, name in auto.CAMERA_NAME_MAP.items():
            v = vids.get(cam)
            lines.append(f"{'OK ' if v else '-- '} {cam}_{name:14s} {os.path.basename(v) if v else 'NOT FOUND'}")
        self.videos_lbl.configure(text="\n".join(lines), foreground=("#1a7f37" if len(vids) == 4 else "#b42318"))

    def _open_output(self) -> None:
        out = os.path.join(self.dataset_var.get().strip(), auto.OUT_DIRNAME)
        if os.path.isdir(out):
            os.startfile(out) if sys.platform == "win32" else None
        else:
            messagebox.showinfo("Output", "No output folder yet.")

    def _append_log(self, text: str) -> None:
        self.txt.configure(state=tk.NORMAL)
        self.txt.insert(tk.END, text)
        self.txt.see(tk.END)
        self.txt.configure(state=tk.DISABLED)

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, payload = self.msg_queue.get_nowait()
                if kind == "log":
                    self._append_log(str(payload))
                elif kind == "progress":
                    stage, frac = payload  # type: ignore[misc]
                    base = {"scan": 0, "write": 70}[stage]
                    span = {"scan": 70, "write": 30}[stage]
                    self.pbar["value"] = base + span * frac
                    self.stage_lbl.configure(text=f"{'Scanning' if stage == 'scan' else 'Writing'} {frac * 100:4.0f}%")
                elif kind == "done":
                    self._set_running(False)
                    ok, msg = payload  # type: ignore[misc]
                    if ok:
                        self.pbar["value"] = 100
                        self.stage_lbl.configure(text="Done")
                        messagebox.showinfo("Finished", msg)
                    else:
                        self.stage_lbl.configure(text="Stopped")
                        if msg:
                            messagebox.showerror("Error", msg)
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    def _set_running(self, running: bool) -> None:
        st = tk.DISABLED if running else tk.NORMAL
        for w in [self.entry_path, self.browse_btn, self.start_btn, self.reuse_chk, self.full_chk,
                  *self.spinboxes, *self.mode_rbs]:
            w.configure(state=st)
        self.cancel_btn.configure(state=tk.NORMAL if running else tk.DISABLED)
        self.margin_sb.configure(state=tk.NORMAL if (self.full_var.get() and not running) else tk.DISABLED)

    # ---------------- actions ----------------
    def _on_start(self) -> None:
        dataset = self.dataset_var.get().strip()
        if not os.path.isdir(dataset):
            messagebox.showwarning("Dataset", "Select a valid folder with the 4 videos.")
            return
        if len(auto.map_videos(dataset)) != 4:
            messagebox.showerror("Dataset", "Could not find the 4 camera videos in that folder.")
            return
        try:
            interval, min_tags, step = int(self.interval_var.get()), int(self.min_tags_var.get()), int(self.scan_step_var.get())
            margin, full = int(self.margin_var.get()), bool(self.full_var.get())
        except (tk.TclError, ValueError):
            messagebox.showerror("Parameters", "Interval, min tags and scan step must be integers.")
            return

        n_old = auto.existing_output_images(dataset)
        if n_old and not messagebox.askyesno(
                "Overwrite?", f"processed_frames/ already has {n_old} images.\nDelete them and generate new ones?"):
            return

        self.cancel_event.clear()
        self._set_running(True)
        self.pbar["value"] = 0
        self._append_log(f"\n=== Started: interval={interval}, min_tags={min_tags}, scan_step={step}, "
                         f"full_board={full}, margin={margin}px, "
                         f"mode={self.mode_var.get()} ===\n")

        def worker() -> None:
            try:
                n = auto.extract_covisible_frames(
                    dataset, interval=interval, min_tags=min_tags, scan_step=step, mode=self.mode_var.get(),
                    full_board=full, margin_px=margin,
                    reuse_scan=self.reuse_var.get(), overwrite=True,
                    log=lambda s: self.msg_queue.put(("log", s)),
                    progress=lambda st, f: self.msg_queue.put(("progress", (st, f))),
                    cancel=self.cancel_event)
                self.msg_queue.put(("done", (True, f"{n} synchronized frame sets written to\n"
                                                   f"{os.path.join(dataset, auto.OUT_DIRNAME)}")))
            except auto.Cancelled:
                self.msg_queue.put(("log", "\n=== Cancelled ===\n"))
                self.msg_queue.put(("done", (False, "")))
            except Exception as e:  # noqa: BLE001
                self.msg_queue.put(("log", f"\n[ERROR] {e}\n"))
                self.msg_queue.put(("done", (False, str(e))))

        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()

    def _on_cancel(self) -> None:
        self.cancel_event.set()
        self.stage_lbl.configure(text="Cancelling…")


def main() -> None:
    AutoCalibGUI().mainloop()


if __name__ == "__main__":
    main()
