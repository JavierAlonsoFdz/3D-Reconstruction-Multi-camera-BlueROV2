#!/usr/bin/env python3

"""
GUI to crop the 4 multicamera videos of a mission and georeference the crop:
- Videos folder (the 4 .mkv downloaded with the ROV GUI)
- Navigation data folder of the same mission
- Start / end inside the video (HH:MM:SS)

Writes, in output/<mission>_<start>_<end>/, the 4 cropped .mp4, a CSV with the
position (date, time, latitude, longitude, depth) at the first and the last frame, and
a *_all_timestamps.csv with the same columns for every navigation row in between.

Usage:
    python gui_georeferencing.py

Requirements: ffmpeg and ffprobe in PATH (standard library otherwise).
"""

from __future__ import annotations
import os
import sys
import threading
import queue
import tkinter as tk
from pathlib import Path
from tkinter import ttk, filedialog, messagebox

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import georeferencing as geo  # type: ignore  # noqa: E402

DEFAULTS = {
    "videos": os.path.join(SCRIPT_DIR, "videos"),
    "navigation": os.path.join(SCRIPT_DIR, "navigation data"),
    "start": "00:00:05",
    "end": "00:00:25",
    "out": os.path.join(SCRIPT_DIR, "output"),
}


class GeoreferencingGUI(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Georeferencing GUI")
        self.geometry("780x600")
        self.minsize(720, 520)

        self.log_queue: queue.Queue[str] = queue.Queue()
        self.cancel_event = threading.Event()
        self.worker_thread: threading.Thread | None = None

        self._build_ui()
        self.after(100, self._poll_log_queue)
        self.after(300, self._inspect)

    def _build_ui(self) -> None:
        pad = {"padx": 10, "pady": 8}
        frm = ttk.Frame(self)
        frm.pack(fill=tk.BOTH, expand=True)

        # Input paths
        paths = ttk.LabelFrame(frm, text="Mission")
        paths.pack(fill=tk.X, **pad)
        self.videos_var = tk.StringVar(value=DEFAULTS["videos"])
        self.nav_var = tk.StringVar(value=DEFAULTS["navigation"])
        self.out_var = tk.StringVar(value=DEFAULTS["out"])
        self.path_entries = []
        for row, (label, var) in enumerate((("Videos", self.videos_var), ("Navigation data", self.nav_var), ("Output", self.out_var))):
            ttk.Label(paths, text=label).grid(row=row, column=0, sticky=tk.W, padx=(10, 6), pady=4)
            entry = ttk.Entry(paths, textvariable=var)
            entry.grid(row=row, column=1, sticky=tk.EW, pady=4)
            ttk.Button(paths, text="Browse…", command=lambda v=var: self._browse(v)).grid(row=row, column=2, padx=6, pady=4)
            self.path_entries.append(entry)
        paths.columnconfigure(1, weight=1)
        helper = (
            "Videos: the folder with the 4 camera .mkv of the mission, downloaded with the ROV GUI (Save Videos), "
            "so their recording date is in the topside clock. Navigation data: the mission folder with "
            "*_cam0_transformed.csv. Output: a folder <mission>_<start>_<end> is created inside it."
        )
        ttk.Label(paths, text=helper, foreground="#555555", wraplength=720, justify=tk.LEFT).grid(
            row=3, column=0, columnspan=3, sticky=tk.W, padx=10, pady=(4, 8)
        )
        self.info_var = tk.StringVar(value="")
        ttk.Label(paths, textvariable=self.info_var, foreground="#1f5fa8", wraplength=720, justify=tk.LEFT).grid(
            row=4, column=0, columnspan=3, sticky=tk.W, padx=10, pady=(0, 8)
        )
        for var in (self.videos_var, self.nav_var):
            var.trace_add("write", lambda *_: self.after(400, self._inspect))

        # Crop
        crop = ttk.LabelFrame(frm, text="Crop (time inside the video)")
        crop.pack(fill=tk.X, **pad)
        self.start_var = tk.StringVar(value=DEFAULTS["start"])
        self.end_var = tk.StringVar(value=DEFAULTS["end"])
        ttk.Label(crop, text="Start").grid(row=0, column=0, sticky=tk.W, padx=(10, 6), pady=8)
        self.start_entry = ttk.Entry(crop, textvariable=self.start_var, width=12)
        self.start_entry.grid(row=0, column=1, sticky=tk.W, pady=8)
        ttk.Label(crop, text="End").grid(row=0, column=2, sticky=tk.W, padx=(20, 6), pady=8)
        self.end_entry = ttk.Entry(crop, textvariable=self.end_var, width=12)
        self.end_entry.grid(row=0, column=3, sticky=tk.W, pady=8)
        ttk.Label(crop, text="HH:MM:SS (decimals allowed, e.g. 00:03:00.5)", foreground="#555555").grid(
            row=0, column=4, sticky=tk.W, padx=(20, 10), pady=8
        )

        # Position
        pos = ttk.LabelFrame(frm, text="Position")
        pos.pack(fill=tk.X, **pad)
        self.position_var = tk.StringVar(value="cam0")
        ttk.Radiobutton(pos, text="cam0 (aux_right)", value="cam0", variable=self.position_var).grid(
            row=0, column=0, sticky=tk.W, padx=10, pady=(8, 2)
        )
        ttk.Label(pos, text="Position of the camera (navigation transformed to cam0).", foreground="#555555").grid(
            row=0, column=1, sticky=tk.W, padx=(6, 10), pady=(8, 2)
        )
        ttk.Radiobutton(pos, text="Vehicle", value="vehicle", variable=self.position_var).grid(
            row=1, column=0, sticky=tk.W, padx=10, pady=(2, 8)
        )
        ttk.Label(pos, text="Position of the ROV itself (IMU).", foreground="#555555").grid(
            row=1, column=1, sticky=tk.W, padx=(6, 10), pady=(2, 8)
        )

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
        self.txt = tk.Text(log_frame, height=12, wrap=tk.WORD, state=tk.DISABLED)
        self.txt.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

    def _browse(self, var: tk.StringVar) -> None:
        path = filedialog.askdirectory(title="Select folder", initialdir=var.get() or SCRIPT_DIR)
        if path:
            var.set(path)

    def _inspect(self) -> None:
        """Show the videos' real start and duration and the navigation span for the selected folders."""
        def work() -> None:
            try:
                videos = geo.find_videos(Path(self.videos_var.get()), log=lambda _m: None)
                ref = videos[geo.REFERENCE_CAMERA]
                text = (f"{len(videos)} videos · start {ref.start:%Y-%m-%d %H:%M:%S} · "
                        f"length {geo.format_offset(ref.duration_s)} ({ref.duration_s:.1f} s) at {ref.fps:g} fps")
                try:
                    nav = geo.find_navigation(Path(self.nav_var.get()), self.position_var.get())
                    fixes = geo.load_fixes(nav)
                    offset = (fixes[0].time - ref.start).total_seconds()
                    text += (f"\nNavigation {geo.mission_name(nav)}: {fixes[0].time:%H:%M:%S} - {fixes[-1].time:%H:%M:%S}"
                             f" (video time {geo.format_offset(max(0.0, offset))} - "
                             f"{geo.format_offset(max(0.0, (fixes[-1].time - ref.start).total_seconds()))})")
                except Exception as error:  # noqa: BLE001
                    text += f"\nNavigation: {error}"
            except Exception as error:  # noqa: BLE001
                text = f"Videos: {error}"
            self.after(0, lambda: self.info_var.set(text))

        threading.Thread(target=work, daemon=True).start()

    def _append_log(self, text: str) -> None:
        self.txt.configure(state=tk.NORMAL)
        self.txt.insert(tk.END, text)
        self.txt.see(tk.END)
        self.txt.configure(state=tk.DISABLED)

    def _poll_log_queue(self) -> None:
        try:
            while True:
                self._append_log(self.log_queue.get_nowait())
        except queue.Empty:
            pass
        self.after(100, self._poll_log_queue)

    def _set_controls_enabled(self, enabled: bool) -> None:
        state = tk.NORMAL if enabled else tk.DISABLED
        for w in [*self.path_entries, self.start_entry, self.end_entry, self.start_btn]:
            try:
                w.configure(state=state)
            except tk.TclError:
                pass
        self.stop_btn.configure(state=(tk.NORMAL if not enabled else tk.DISABLED))

    def _on_start(self) -> None:
        videos, nav = self.videos_var.get().strip(), self.nav_var.get().strip()
        if not os.path.isdir(videos) or not os.path.exists(nav):
            messagebox.showerror("Invalid path", "Select an existing videos folder and navigation data folder.")
            return
        try:
            geo.parse_time(self.start_var.get())
            geo.parse_time(self.end_var.get())
        except ValueError as error:
            messagebox.showerror("Invalid time", str(error))
            return

        self.cancel_event.clear()
        self._set_controls_enabled(False)
        self._append_log("\n=== Process started ===\n")
        args = (Path(videos), Path(nav), self.start_var.get(), self.end_var.get(),
                Path(self.out_var.get()) if self.out_var.get().strip() else None, self.position_var.get())

        def worker() -> None:
            try:
                out_dir = geo.run(*args, log=lambda m: self.log_queue.put(m + "\n"), cancelled=self.cancel_event.is_set)
                self.log_queue.put("\n=== Process finished successfully ===\n")
                self.after(0, lambda: messagebox.showinfo("Done", f"Output written to:\n{out_dir}"))
            except Exception as error:  # noqa: BLE001
                self.log_queue.put(f"\n[ERROR] {error}\n")
                if str(error) != "cancelled":
                    self.after(0, lambda: messagebox.showerror("Processing error", str(error)))
            finally:
                self.after(0, lambda: self._set_controls_enabled(True))

        self.worker_thread = threading.Thread(target=worker, daemon=True)
        self.worker_thread.start()

    def _on_cancel(self) -> None:
        self.cancel_event.set()
        self._append_log("Cancelling after stopping ffmpeg…\n")


def main() -> None:
    app = GeoreferencingGUI()
    app.mainloop()


if __name__ == "__main__":
    main()
