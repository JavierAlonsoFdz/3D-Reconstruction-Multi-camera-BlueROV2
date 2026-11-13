#!/usr/bin/env python3
"""
Automatic Kalibr frame extraction: keep only the moments where the AprilGrid
is visible in ALL 4 cameras.

Pipeline
  1) Scan  : every `scan_step` frames of each video, count detected AprilGrid tags
             (tag36h11 with Kalibr's 2-bit black border). One thread per camera.
  2) Select: a frame is "covisible" if every camera sees >= `min_tags` tags AND (full_board)
             the WHOLE 6x6 AprilGrid lies inside the image of every camera (outer board
             contour projected with a homography fitted on the detected tags, >= margin_px
             from the image border).
             A covisible frame is kept if it is >= `interval` frames after the last
             kept one -> continuous runs give one frame every `interval`, isolated
             views still give one frame, no near-duplicates.
  3) Write : save the selected frames (raw / turbid / clear filter) with the same
             layout and naming as the Calibration GUI:
                 processed_frames/cam0_aux_right/0000000100000000.jpg ...
             plus processed_frames/detections.csv (per-frame tag counts + selection).

CLI:
    python main_automatic.py <dataset_folder> [--interval 10] [--min-tags 8] [--scan-step 5]
                             [--mode raw|turbid|clear] [--reuse-scan] [--overwrite]
"""
from __future__ import annotations

import argparse
import csv
import glob
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, List, Optional, Tuple

import cv2
import numpy as np

import filter as filt

CAMERA_NAME_MAP: Dict[str, str] = {
    "cam0": "aux_right",
    "cam1": "aux_left",
    "cam2": "bottom_most",
    "cam3": "stereo_bottom",
}
# Target (april_6x6_80x80cm.yaml): aprilgrid 6x6, tagSize 0.088 m, tagSpacing 0.3, codeOffset 0
TAG_COLS, TAG_ROWS = 6, 6
TAG_SIZE = 0.088
TAG_SPACING = 0.3
NUM_TAGS = TAG_COLS * TAG_ROWS
MIN_SPREAD = 3  # detected tags must span >= 4 of the 6 rows and columns
NO_BOARD = -9999  # board margin value when the full board cannot be confirmed
OUT_DIRNAME = "processed_frames"
CSV_NAME = "detections.csv"

LogFn = Callable[[str], None]
ProgressFn = Callable[[str, float], None]  # (stage, fraction 0..1)


class Cancelled(Exception):
    pass


# ---------------- helpers ----------------
def make_detector() -> cv2.aruco.ArucoDetector:
    p = cv2.aruco.DetectorParameters()
    p.markerBorderBits = 2  # Kalibr aprilgrid tags have a 2-bit black border
    p.adaptiveThreshWinSizeMin = 5
    p.adaptiveThreshWinSizeMax = 53
    p.adaptiveThreshWinSizeStep = 8
    p.errorCorrectionRate = 0.8
    p.maxErroneousBitsInBorderRate = 0.5
    return cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11), p)


def board_margin(corners, ids, img_w: int, img_h: int) -> int:
    """
    How far (px) the WHOLE AprilGrid lies inside the image.

    - Requires the detected tags to span >= MIN_SPREAD+1 rows and columns of the grid.
    - Fits a homography grid->image from the detected tag corners (RANSAC) and projects the
      outer contour of the board (tags + the black corner squares around them).
    Returns the min distance of those 4 projected corners to the image border
    (negative = board cut by the image edge), or NO_BOARD if it cannot be confirmed.
    """
    if ids is None:
        return NO_BOARD
    ids = ids.flatten()
    keep = [k for k, i in enumerate(ids) if 0 <= i < NUM_TAGS]
    if len(keep) < 4:
        return NO_BOARD
    rows = {int(ids[k]) // TAG_COLS for k in keep}
    cols = {int(ids[k]) % TAG_COLS for k in keep}
    # tags must spread over the board (>= 4 rows and 4 cols) so the extrapolation to the
    # board edges is well constrained even when turbidity hides the outer tags
    if max(rows) - min(rows) < MIN_SPREAD or max(cols) - min(cols) < MIN_SPREAD:
        return NO_BOARD
    s, pitch = TAG_SIZE, TAG_SIZE * (1 + TAG_SPACING)
    # fit on tag centres (independent of the tag corner order / board axis orientation)
    centres_b = np.array([[(int(ids[k]) % TAG_COLS) * pitch, (int(ids[k]) // TAG_COLS) * pitch] for k in keep],
                         np.float32)
    centres_i = np.array([corners[k].reshape(4, 2).mean(axis=0) for k in keep], np.float32)
    H, _ = cv2.findHomography(centres_b, centres_i, cv2.RANSAC, 15.0)
    if H is None:
        return NO_BOARD
    ext = s / 2 + TAG_SPACING * s  # from outer tag centre to outer edge of the black corner squares
    x1, y1 = (TAG_COLS - 1) * pitch + ext, (TAG_ROWS - 1) * pitch + ext
    outer = np.array([[[-ext, -ext]], [[x1, -ext]], [[x1, y1]], [[-ext, y1]]], np.float32)
    pts = cv2.perspectiveTransform(outer, H).reshape(4, 2)
    if not np.all(np.isfinite(pts)):
        return NO_BOARD
    m = np.minimum.reduce([pts[:, 0], pts[:, 1], img_w - 1 - pts[:, 0], img_h - 1 - pts[:, 1]])
    return int(np.floor(m.min()))


def map_videos(dataset_path: str) -> Dict[str, str]:
    """camera id -> video path, matched by the camera token in the filename."""
    out: Dict[str, str] = {}
    for f in sorted(os.listdir(dataset_path)):
        if f.lower().endswith((".mp4", ".avi", ".mov", ".mkv")):
            base = os.path.splitext(f)[0].lower()
            for cam, name in CAMERA_NAME_MAP.items():
                if base.endswith(name) or f"_{name}_" in base or base == name:
                    out[cam] = os.path.join(dataset_path, f)
    return out


def video_info(path: str) -> tuple[int, float]:
    cap = cv2.VideoCapture(path)
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    cap.release()
    return n, fps


def apply_filter(frame, mode: str):
    if mode == "turbid":
        return filt.gentle_color_restore(frame)
    if mode == "clear":
        return filt.enhance_clear_water(frame)
    return frame


# ---------------- stage 1: scan ----------------
def _scan_video(cam: str, path: str, step: int, total: int, prog: Dict[str, float],
                cancel: threading.Event) -> Dict[int, Tuple[int, int]]:
    det = make_detector()
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open {path}")
    counts: Dict[int, Tuple[int, int]] = {}  # frame -> (n tags, board margin px)
    idx = 0
    try:
        while True:
            if cancel.is_set():
                raise Cancelled()
            if idx % step == 0:
                ok, frame = cap.read()
                if not ok:
                    break
                corners, ids, _ = det.detectMarkers(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
                n = 0 if ids is None else int(sum(1 for i in ids.flatten() if 0 <= i < NUM_TAGS))
                counts[idx] = (n, board_margin(corners, ids, frame.shape[1], frame.shape[0]))
                prog[cam] = min(1.0, idx / max(total, 1))
            elif not cap.grab():
                break
            idx += 1
    finally:
        cap.release()
    prog[cam] = 1.0
    return counts


def load_scan(csv_path: str, step: int) -> Optional[Dict[str, Dict[int, Tuple[int, int]]]]:
    """Read (tags, board margin) from a previous detections.csv (None if incompatible)."""
    if not os.path.isfile(csv_path):
        return None
    with open(csv_path, newline="") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        return None
    cols = {cam: (f"{cam}_{name}_tags", f"{cam}_{name}_board_margin_px") for cam, name in CAMERA_NAME_MAP.items()}
    if any(c not in rows[0] for pair in cols.values() for c in pair):
        return None
    frames = [int(r["frame"]) for r in rows]
    if len(frames) > 1 and frames[1] - frames[0] != step:
        return None
    return {cam: {int(r["frame"]): (int(r[ct]), int(r[cm])) for r in rows} for cam, (ct, cm) in cols.items()}


# ---------------- stage 2: select ----------------
def frame_ok(tags: int, margin: int, min_tags: int, full_board: bool, margin_px: int) -> bool:
    return tags >= min_tags and (not full_board or margin >= margin_px)


def select_frames(counts: Dict[str, Dict[int, Tuple[int, int]]], step: int, min_tags: int, interval: int,
                  full_board: bool = True, margin_px: int = 10):
    common = sorted(set.intersection(*(set(c) for c in counts.values())))
    covis = [f for f in common
             if all(frame_ok(*counts[c][f], min_tags, full_board, margin_px) for c in counts)]
    segments: List[List[int]] = []
    for f in covis:
        if segments and f - segments[-1][-1] == step:
            segments[-1].append(f)
        else:
            segments.append([f])
    selected: List[int] = []
    for f in covis:
        if not selected or f - selected[-1] >= interval:
            selected.append(f)
    return common, covis, segments, selected


# ---------------- stage 3: write ----------------
def _write_frames(cam: str, path: str, frames: List[int], out_dir: str, mode: str,
                  prog: Dict[str, float], cancel: threading.Event) -> int:
    wanted = {f: k + 1 for k, f in enumerate(frames)}  # frame index -> 1-based set index
    last = max(frames) if frames else -1
    os.makedirs(out_dir, exist_ok=True)
    cap = cv2.VideoCapture(path)
    idx, written = 0, 0
    try:
        while idx <= last:
            if cancel.is_set():
                raise Cancelled()
            if idx in wanted:
                ok, frame = cap.read()
                if not ok:
                    break
                out = apply_filter(frame, mode)
                cv2.imwrite(os.path.join(out_dir, f"{wanted[idx]:08d}00000000.jpg"), out,
                            [cv2.IMWRITE_JPEG_QUALITY, 95])
                written += 1
                prog[cam] = written / len(frames)
            elif not cap.grab():
                break
            idx += 1
    finally:
        cap.release()
    return written


def existing_output_images(dataset_path: str, out_root: Optional[str] = None) -> int:
    out_root = out_root or os.path.join(dataset_path, OUT_DIRNAME)
    return sum(len(glob.glob(os.path.join(out_root, f"{c}_{n}", "*.jpg"))) for c, n in CAMERA_NAME_MAP.items())


def _run_parallel(fn, jobs, prog: Dict[str, float], stage: str, progress: Optional[ProgressFn],
                  cancel: threading.Event):
    with ThreadPoolExecutor(len(jobs)) as ex:
        futs = {cam: ex.submit(fn, *args) for cam, args in jobs.items()}
        while not all(f.done() for f in futs.values()):
            if progress:
                progress(stage, sum(prog.values()) / len(jobs))
            cancel.wait(0.5)
        if progress:
            progress(stage, 1.0)
        return {cam: f.result() for cam, f in futs.items()}  # re-raises worker errors


# ---------------- main entry ----------------
def extract_covisible_frames(
    dataset_path: str,
    *,
    interval: int = 10,
    min_tags: int = 8,
    full_board: bool = True,
    margin_px: int = 10,
    scan_step: int = 5,
    mode: str = "raw",
    reuse_scan: bool = False,
    overwrite: bool = False,
    out_root: Optional[str] = None,
    log: LogFn = print,
    progress: Optional[ProgressFn] = None,
    cancel: Optional[threading.Event] = None,
) -> int:
    """Run the full pipeline. Returns the number of synchronized frame sets written."""
    cancel = cancel or threading.Event()
    if interval <= 0 or scan_step <= 0 or min_tags <= 0:
        raise ValueError("interval, scan_step and min_tags must be > 0")
    if interval % scan_step:
        log(f"[WARN] interval ({interval}) is not a multiple of scan step ({scan_step}); "
            f"spacing will be >= {interval} but not exact.\n")

    videos = map_videos(dataset_path)
    if len(videos) != 4:
        raise RuntimeError(f"Expected 4 camera videos ({', '.join(CAMERA_NAME_MAP.values())}), found {len(videos)}: "
                           + ", ".join(os.path.basename(v) for v in videos.values()))
    out_root = out_root or os.path.join(dataset_path, OUT_DIRNAME)
    csv_path = os.path.join(out_root, CSV_NAME)

    n_old = existing_output_images(dataset_path, out_root)
    if n_old and not overwrite:
        raise RuntimeError(f"{out_root} already contains {n_old} images. Enable overwrite or choose another folder.")

    infos = {cam: video_info(p) for cam, p in videos.items()}
    fps = next(iter(infos.values()))[1]
    for cam in sorted(videos):
        n, f = infos[cam]
        log(f"{cam}_{CAMERA_NAME_MAP[cam]:14s} {os.path.basename(videos[cam])}  ({n} frames, {f:.0f} fps)\n")
    if len({n for n, _ in infos.values()}) > 1:
        log("[WARN] the 4 videos have different frame counts; only common frame indices are used.\n")

    # 1) scan
    counts = load_scan(csv_path, scan_step) if reuse_scan else None
    if counts is not None:
        log(f"\n[1/3] Reusing previous detection scan from {csv_path}\n")
    else:
        if reuse_scan:
            log("\n[1/3] No compatible previous scan found -> scanning.\n")
        log(f"\n[1/3] Scanning AprilGrid every {scan_step} frames in the 4 videos (this takes a few minutes)...\n")
        prog = {cam: 0.0 for cam in videos}
        counts = _run_parallel(
            _scan_video,
            {cam: (cam, videos[cam], scan_step, infos[cam][0], prog, cancel) for cam in sorted(videos)},
            prog, "scan", progress, cancel)

    # 2) select
    common, covis, segments, selected = select_frames(counts, scan_step, min_tags, interval, full_board, margin_px)
    crit = f">= {min_tags} tags" + (f" + FULL board inside image (margin >= {margin_px}px)" if full_board else "")
    log(f"\n[2/3] Covisible (all 4 cams, {crit}): {len(covis)}/{len(common)} samples, "
        f"{len(segments)} segments\n")
    for seg in segments:
        if seg[-1] - seg[0] >= 2 * interval:
            log(f"   run {seg[0]:6d}-{seg[-1]:6d}  ({seg[0] / fps:6.1f}s - {seg[-1] / fps:6.1f}s, "
                f"{(seg[-1] - seg[0]) / fps:4.1f}s)\n")
    n_short = sum(1 for s in segments if s[-1] - s[0] < 2 * interval)
    log(f"   + {n_short} short/isolated views\n")
    log(f"   -> {len(selected)} synchronized frame sets selected (one every >= {interval} frames)\n")
    if not selected:
        raise RuntimeError("No frame is covisible in the 4 cameras. Try a lower 'min tags' / margin.")

    # clean previous output (only the images we generate)
    os.makedirs(out_root, exist_ok=True)
    if n_old:
        for cam, name in CAMERA_NAME_MAP.items():
            for f in glob.glob(os.path.join(out_root, f"{cam}_{name}", "*.jpg")):
                os.remove(f)
        log(f"   removed {n_old} old images from {out_root}\n")

    with open(csv_path, "w", newline="") as fh:
        w = csv.writer(fh)
        cams = sorted(counts)
        w.writerow(["frame", "time_s",
                    *[f"{c}_{CAMERA_NAME_MAP[c]}_{k}" for c in cams for k in ("tags", "board_margin_px")],
                    "covisible", "selected", "set_index"])
        sel_idx = {f: k + 1 for k, f in enumerate(selected)}
        covis_set = set(covis)
        for f in common:
            w.writerow([f, f"{f / fps:.3f}", *[v for c in cams for v in counts[c][f]], int(f in covis_set),
                        int(f in sel_idx), sel_idx.get(f, "")])

    # 3) write
    log(f"\n[3/3] Writing {len(selected)} x 4 images (filter: {mode})...\n")
    prog = {cam: 0.0 for cam in videos}
    written = _run_parallel(
        _write_frames,
        {cam: (cam, videos[cam], selected, os.path.join(out_root, f"{cam}_{CAMERA_NAME_MAP[cam]}"), mode, prog, cancel)
         for cam in sorted(videos)},
        prog, "write", progress, cancel)
    for cam in sorted(written):
        log(f"   {cam}_{CAMERA_NAME_MAP[cam]}: {written[cam]} images\n")
    if len(set(written.values())) > 1:
        log("[WARN] cameras have different image counts!\n")
    log(f"\nDone -> {out_root}\n")
    return min(written.values())


# ---------------- CLI ----------------
def create_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Automatic covisible frame extraction for Kalibr")
    p.add_argument("dataset_path", help="Folder with the 4 camera videos")
    p.add_argument("--interval", type=int, default=10, help="Keep one frame every N frames inside covisible runs")
    p.add_argument("--min-tags", type=int, default=8, help="Min AprilGrid tags each camera must see")
    p.add_argument("--allow-partial", action="store_true",
                   help="Do NOT require the whole board inside the image (only --min-tags)")
    p.add_argument("--margin-px", type=int, default=10,
                   help="Min distance (px) of the full board contour to the image border")
    p.add_argument("--scan-step", type=int, default=5, help="Detection sampling step (should divide --interval)")
    p.add_argument("--mode", choices=["raw", "turbid", "clear"], default="raw")
    p.add_argument("--reuse-scan", action="store_true", help="Reuse tag counts from a previous detections.csv")
    p.add_argument("--overwrite", action="store_true", help="Replace images already in processed_frames/")
    return p


if __name__ == "__main__":
    a = create_parser().parse_args()
    extract_covisible_frames(a.dataset_path, interval=a.interval, min_tags=a.min_tags, scan_step=a.scan_step,
                             full_board=not a.allow_partial, margin_px=a.margin_px,
                             mode=a.mode, reuse_scan=a.reuse_scan, overwrite=a.overwrite,
                             log=lambda s: print(s, end="", flush=True))
