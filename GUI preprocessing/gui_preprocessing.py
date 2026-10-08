#!/usr/bin/env python3

"""
GUI to run the preprocessing pipeline with options for:
- Dataset folder (with the 4 camera videos)
- Keyframe mode: auto or fixed (with interval)
- Filtering mode: raw, turbid, clear

Usage:
    python gui_preprocessing.py

Requirements: OpenCV, numpy, and the project modules installed/available.
"""

from __future__ import annotations
import os
import sys
import threading
import queue
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import signal

# Ensure we can import the main module when this script is in the same directory
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from main_preprocessing import extract_frames_with_keyframes, create_parser  # type: ignore


# Defaults sourced from main_preprocessing.create_parser() so they stay in sync
_PARSER = create_parser()
_CLI_DEFAULTS = {}
for _act in _PARSER._actions:  # type: ignore[attr-defined]
    # skip help action; collect defined defaults
    if getattr(_act, "dest", None) and getattr(_act, "default", None) is not None:
        _CLI_DEFAULTS[_act.dest] = _act.default

DEFAULTS = {
    "mode": _CLI_DEFAULTS.get("mode", "raw"),
    "keyframe_mode": _CLI_DEFAULTS.get("keyframe_mode", "auto"),
    "fixed_interval": _CLI_DEFAULTS.get("fixed_interval", 10),
    # map CLI names to function parameter names
    "min_disp_px": _CLI_DEFAULTS.get("min_disp", 100.0),
    "min_inliers": _CLI_DEFAULTS.get("min_inliers", 90),
    "inlier_ratio_thresh": _CLI_DEFAULTS.get("inlier_ratio_thresh", 0.8),
}


class TextQueueWriter:
    """Redirects text to a queue to be consumed by the GUI thread."""

    def __init__(self, q: queue.Queue[str]):
        self.q = q

    def write(self, s: str) -> None:
        if s:
            self.q.put(s)

    def flush(self) -> None:
        pass


class PreprocGUI(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Preprocessing GUI")
        self.geometry("760x520")
        self.minsize(720, 480)

        self._build_ui()

        # Queue + timer for logs
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.after(100, self._poll_log_queue)

        # Worker thread
        self.worker_thread: threading.Thread | None = None

    def _build_ui(self) -> None:
        pad = {"padx": 10, "pady": 8}

        frm = ttk.Frame(self)
        frm.pack(fill=tk.BOTH, expand=True)

        # Dataset path
        path_frame = ttk.LabelFrame(frm, text="Dataset")
        path_frame.pack(fill=tk.X, **pad)

        self.dataset_var = tk.StringVar()
        top_row = ttk.Frame(path_frame)
        top_row.pack(fill=tk.X, padx=4, pady=(8, 0))

        self.entry_path = ttk.Entry(top_row, textvariable=self.dataset_var)
        self.entry_path.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 6))
        ttk.Button(top_row, text="Browse…", command=self._browse_folder).pack(side=tk.LEFT, padx=(0, 6))

        # Helper text for dataset requirements
        helper = (
            "Select the folder that contains the 4 camera videos. "
            "Filenames must include the camera tokens to be detected: "
            "aux_right, aux_left, bottom_most, stereo_bottom."
        )
        ttk.Label(path_frame, text=helper, foreground="#555555", wraplength=700, justify=tk.LEFT).pack(
            fill=tk.X, padx=12, pady=(6, 10)
        )

        # Keyframe mode frame
        kf_frame = ttk.LabelFrame(frm, text="Keyframes")
        kf_frame.pack(fill=tk.X, **pad)

        self.kf_mode = tk.StringVar(value=DEFAULTS["keyframe_mode"])  # auto|fixed

        # Row 0: Auto radio + short description
        rb_auto = ttk.Radiobutton(
            kf_frame,
            text="Auto",
            value="auto",
            variable=self.kf_mode,
            command=self._on_kf_mode_change,
        )
        rb_auto.grid(row=0, column=0, sticky=tk.W, padx=10, pady=(8, 2))
        ttk.Label(
            kf_frame,
            text="Automatic keyframe selection based on optical flow and feature matching.",
            wraplength=560,
            justify=tk.LEFT,
            foreground="#555555",
        ).grid(row=0, column=1, sticky=tk.W, padx=(6, 10), pady=(8, 2))

        # Row 1: Fixed radio + description + interval control
        rb_fixed = ttk.Radiobutton(
            kf_frame,
            text="Fixed",
            value="fixed",
            variable=self.kf_mode,
            command=self._on_kf_mode_change,
        )
        rb_fixed.grid(row=1, column=0, sticky=tk.W, padx=10, pady=(2, 8))
        ttk.Label(
            kf_frame,
            text="Save one frame every N frames (uniform sampling).",
            wraplength=420,
            justify=tk.LEFT,
            foreground="#555555",
        ).grid(row=1, column=1, sticky=tk.W, padx=(6, 10), pady=(2, 8))

        ttk.Label(kf_frame, text="Interval (N):").grid(row=1, column=2, sticky=tk.E, padx=(10, 6))
        self.fixed_interval_var = tk.IntVar(value=DEFAULTS["fixed_interval"])
        self.fixed_entry = ttk.Spinbox(kf_frame, from_=1, to=9999, textvariable=self.fixed_interval_var, width=8)
        self.fixed_entry.grid(row=1, column=3, sticky=tk.W, padx=(0, 6))

        for i in range(4):
            kf_frame.columnconfigure(i, weight=0)

        # Filter mode frame
        flt_frame = ttk.LabelFrame(frm, text="Filtering")
        flt_frame.pack(fill=tk.X, **pad)

        self.mode_var = tk.StringVar(value=DEFAULTS["mode"])  # raw|turbid|clear

        # Row per filtering option with short description
        # Raw
        ttk.Radiobutton(flt_frame, text="Raw", value="raw", variable=self.mode_var).grid(
            row=0, column=0, sticky=tk.W, padx=10, pady=(8, 2)
        )
        ttk.Label(
            flt_frame,
            text="No filtering. Save frames as-is.",
            wraplength=560,
            justify=tk.LEFT,
            foreground="#555555",
        ).grid(row=0, column=1, sticky=tk.W, padx=(6, 10), pady=(8, 2))

        # Turbid
        ttk.Radiobutton(flt_frame, text="Turbid", value="turbid", variable=self.mode_var).grid(
            row=1, column=0, sticky=tk.W, padx=10, pady=(2, 2)
        )
        ttk.Label(
            flt_frame,
            text="White balance + CLAHE for turbid/low-visibility water, with mild sharpening.",
            wraplength=560,
            justify=tk.LEFT,
            foreground="#555555",
        ).grid(row=1, column=1, sticky=tk.W, padx=(6, 10), pady=(2, 2))

        # Clear
        ttk.Radiobutton(flt_frame, text="Clear", value="clear", variable=self.mode_var).grid(
            row=2, column=0, sticky=tk.W, padx=10, pady=(2, 8)
        )
        ttk.Label(
            flt_frame,
            text="Enhancement for clear water (subtle sharpening/contrast).",
            wraplength=560,
            justify=tk.LEFT,
            foreground="#555555",
        ).grid(row=2, column=1, sticky=tk.W, padx=(6, 10), pady=(2, 8))

        # Control buttons
        btn_frame = ttk.Frame(frm)
        btn_frame.pack(fill=tk.X, **pad)
        self.start_btn = ttk.Button(btn_frame, text="Start", command=self._on_start)
        self.start_btn.pack(side=tk.LEFT)
        self.stop_btn = ttk.Button(btn_frame, text="Cancel", command=self._on_cancel, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=(10, 0))

        # Log area
        log_frame = ttk.LabelFrame(frm, text="Log")
        log_frame.pack(fill=tk.BOTH, expand=True, **pad)
        self.txt = tk.Text(log_frame, height=14, wrap=tk.WORD, state=tk.DISABLED)
        self.txt.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        self._on_kf_mode_change()

    def _browse_folder(self) -> None:
        path = filedialog.askdirectory(title="Select dataset folder")
        if path:
            self.dataset_var.set(path)

    def _on_kf_mode_change(self) -> None:
        fixed = (self.kf_mode.get() == "fixed")
        state = tk.NORMAL if fixed else tk.DISABLED
        self.fixed_entry.configure(state=state)

    def _append_log(self, text: str) -> None:
        self.txt.configure(state=tk.NORMAL)
        self.txt.insert(tk.END, text)
        self.txt.see(tk.END)
        self.txt.configure(state=tk.DISABLED)

    def _poll_log_queue(self) -> None:
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self._append_log(msg)
        except queue.Empty:
            pass
        self.after(100, self._poll_log_queue)

    def _set_controls_enabled(self, enabled: bool) -> None:
        state = tk.NORMAL if enabled else tk.DISABLED
        for w in [self.entry_path, self.fixed_entry, self.start_btn]:
            try:
                w.configure(state=state)
            except tk.TclError:
                pass
        self.stop_btn.configure(state=(tk.NORMAL if not enabled else tk.DISABLED))

    def _on_start(self) -> None:
        dataset = self.dataset_var.get().strip()
        if not dataset:
            messagebox.showwarning("Missing path", "Please select the dataset folder.")
            return
        if not os.path.isdir(dataset):
            messagebox.showerror("Invalid path", "The selected folder does not exist.")
            return

        mode = self.mode_var.get()
        kf_mode = self.kf_mode.get()
        fixed_interval = int(self.fixed_interval_var.get()) if kf_mode == "fixed" else DEFAULTS["fixed_interval"]

        # Run the work in a thread to keep the GUI responsive
        self._set_controls_enabled(False)
        self._append_log("\n=== Process started ===\n")

        def worker():
            # Redirect stdout so pipeline prints appear in the GUI
            old_stdout = sys.stdout
            sys.stdout = TextQueueWriter(self.log_queue)
            try:
                extract_frames_with_keyframes(
                    dataset,
                    mode=mode,
                    keyframe_mode=kf_mode,
                    fixed_interval=fixed_interval,
                    min_disp_px=DEFAULTS["min_disp_px"],
                    min_inliers=DEFAULTS["min_inliers"],
                    inlier_ratio_thresh=DEFAULTS["inlier_ratio_thresh"],
                )
                self.log_queue.put("\n=== Process finished successfully ===\n")
            except Exception as e:
                self.log_queue.put(f"\n[ERROR] {e}\n")
                messagebox.showerror("Processing error", str(e))
            finally:
                sys.stdout = old_stdout
                # Re-enable controls
                self.after(0, lambda: self._set_controls_enabled(True))

        self.worker_thread = threading.Thread(target=worker, daemon=True)
        self.worker_thread.start()

    def _on_cancel(self) -> None:
        # No cooperative cancellation in the pipeline yet.
        os.kill(os.getpid(), signal.SIGINT)
        #messagebox.showinfo("Cancel", "Hot cancellation is not implemented yet. Please wait until it finishes.")


def main() -> None:
    app = PreprocGUI()
    app.mainloop()


if __name__ == "__main__":
    main()
