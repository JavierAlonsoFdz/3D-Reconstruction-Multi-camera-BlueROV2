#!/usr/bin/env python3

from __future__ import annotations
import os
import cv2
import argparse
from typing import Dict
import numpy as np
import numpy.typing as npt

import filter as filt


# -------- CAMERA NAME MAP --------
CAMERA_NAME_MAP: Dict[str, str] = {
    "cam0": "aux_right",
    "cam1": "aux_left",
    "cam2": "bottom_most",
    "cam3": "stereo_bottom",
}


# --------- Helpers ---------
def map_videos(dataset_path: str) -> Dict[str, str]:
    """
    Scan dataset_path and map camera ids -> video file paths using CAMERA_NAME_MAP.
    """
    files: Dict[str, str] = {}
    for file in sorted(os.listdir(dataset_path)):
        if file.lower().endswith((".mp4", ".avi", ".mov", ".mkv")):
            basename = os.path.splitext(file)[0].lower()
            for cam_id, cam_name in CAMERA_NAME_MAP.items():
                if cam_name in basename:
                    files[cam_id] = os.path.join(dataset_path, file)
                    break
    return files


# -------- MAIN: extract + filter ---------
def extract_frames_with_keyframes(
    dataset_path: str,
    *,
    mode: str,
    fixed_interval: int,
) -> None:
    """
    Fixed-interval frame extraction and per-frame filtering for Kalibr calibration.

    Parameters:
        dataset_path: folder with 4 camera videos.
        mode: "raw", "clear" or "turbid" (user-chosen filter).
        fixed_interval: save one synchronized frame set every N frames.
    """
    video_files: Dict[str, str] = map_videos(dataset_path)
    if len(video_files) != 4:
        raise RuntimeError(f"Expected 4 camera videos, found {len(video_files)}: {video_files}")

    if fixed_interval <= 0:
        raise ValueError("fixed_interval must be greater than 0")

    out_folder: str = os.path.join(dataset_path, "processed_frames")
    os.makedirs(out_folder, exist_ok=True)

    # open captures
    caps: Dict[str, cv2.VideoCapture] = {cam: cv2.VideoCapture(path) for cam, path in video_files.items()}
    for cam, cap in caps.items():
        if not cap.isOpened():
            raise RuntimeError(f"Cannot open {video_files[cam]}")

    # prepare per-camera folders
    for cam_id in video_files:
        cam_name: str = CAMERA_NAME_MAP.get(cam_id, cam_id)
        os.makedirs(os.path.join(out_folder, f"{cam_id}_{cam_name}"), exist_ok=True)

    frame_no: int = 0
    saved: int = 0

    while True:
        frames: Dict[str, npt.NDArray[np.uint8]] = {}
        all_done: bool = True

        for cam, cap in caps.items():
            ret, frame = cap.read()
            if not ret:
                continue
            all_done = False
            # collect frames only if we will save on this frame
            should_save = frame_no % fixed_interval == 0
            if should_save:
                frames[cam] = frame  # frame is ndarray (H,W,3) uint8

        if all_done:
            break

        # Save when we have frames from all 4 cams and the frame is selected
        should_save_now = len(frames) == 4 and frame_no % fixed_interval == 0
        if should_save_now:
            for cam_id, frame in frames.items():
                cam_name: str = CAMERA_NAME_MAP.get(cam_id, cam_id)
                cam_folder: str = os.path.join(out_folder, f"{cam_id}_{cam_name}")

                # apply chosen filter (user asked to pick "clear" or "turbid")
                if mode == "turbid":
                    out_img: npt.NDArray[np.uint8] = filt.gentle_color_restore(frame)

                elif mode == "clear":
                    out_img = filt.enhance_clear_water(frame)

                else:
                    out_img= frame
                # Save final image as JPEG with the required naming scheme:
                #  - 8-digit zero-padded index, followed by 8 zeros, with .jpg extension (for Kalibr requirements)
                #  - Example for first images: 0000000100000000.jpg, 0000000200000000.jpg, ...
                # Use the "saved" counter so all cameras in a set share the same index
                out_filename = f"{saved + 1:08d}00000000.jpg"
                out_path: str = os.path.join(cam_folder, out_filename)
                cv2.imwrite(out_path, out_img)

                print(f"[SAVE] cam={cam_id} frame={frame_no} mode={mode} fixed_interval={fixed_interval}")

            saved += 1

        frame_no += 1

    for cap in caps.values():
        cap.release()

    print(f"Done. Saved {saved} frame sets to {out_folder}")


# -------- CLI  ---------
def create_parser() -> argparse.ArgumentParser:
    """
    Create and return the ArgumentParser used by this module.
    Exposing this allows other modules (e.g., the GUI) to read default values
    without duplicating them.
    """
    p = argparse.ArgumentParser(description="Calibration preprocessing: fixed-interval extraction + filters")
    p.add_argument("dataset_path", type=str, help="Folder with the 4 camera videos")

    # defaults live ONLY here
    p.add_argument("--mode", type=str, choices=["clear", "turbid", "raw"], default="raw")
    p.add_argument("--fixed-interval", "--fixed_interval", type=int, default=10,
                   help="Save one frame every N frames (N>0)")
    return p


def parse_args() -> argparse.Namespace:
    p = create_parser()
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()

    extract_frames_with_keyframes(
        args.dataset_path,
        mode=args.mode,
        fixed_interval=args.fixed_interval,
    )
