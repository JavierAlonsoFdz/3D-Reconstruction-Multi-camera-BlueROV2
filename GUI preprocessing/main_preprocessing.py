#!/usr/bin/env python3

from __future__ import annotations
import os
import cv2
import argparse
from typing import Dict, List
import numpy as np
import numpy.typing as npt

from key_frame_selection_auto import compute_keyframes_by_flow_sparse_simple, thin_keyframes
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
    keyframe_mode: str,
    fixed_interval: int,
    min_disp_px: float,
    min_inliers: int,
    inlier_ratio_thresh: float,
    nav_path: str | None = None,
) -> None:
    """
    Keyframe selection and per-keyframe filtering.

    Parameters:
        dataset_path: folder with 4 camera videos.
        mode: "clear" or "turbid" (user-chosen filter).
        min_disp_px, min_inliers, inlier_ratio_thresh: numeric parameters for keyframe selection.
        nav_path: optional navigation CSV (or the transect folder holding it; *_frames.csv from the GUI
            Navigation Transects preferred). If given, EVERY saved frame of every camera is written as a
            JPG with GPS EXIF (cam0 from the CSV, cam1..cam3 placed with the rig lever arms rotated by the
            ROV attitude); geotags.csv (what was written) and external_georeferencing/ (Cesium
            tileset.json) go next to processed_frames. Without it, frames are saved as PNG.

    processed_frames/ holds only the 4 camera folders; keyframes.txt is written next to it.
    """
    video_files: Dict[str, str] = map_videos(dataset_path)
    if len(video_files) != 4:
        raise RuntimeError(f"Expected 4 camera videos, found {len(video_files)}: {video_files}")

    ref_cam: str = "cam0" if "cam0" in video_files else sorted(video_files.keys())[0]

    # Decide keyframe strategy: auto (optical flow) vs fixed interval
    keyframes: List[int] = []
    keyframe_reasons: Dict[int, str] = {}
    use_auto: bool = (keyframe_mode.lower() == "auto")

    out_folder: str = os.path.join(dataset_path, "processed_frames")
    os.makedirs(out_folder, exist_ok=True)

    if use_auto:
        # compute keyframes using the external module (typed returns List[int])
        keyframes, keyframe_reasons = compute_keyframes_by_flow_sparse_simple(
            video_files[ref_cam],
            min_disp_px=min_disp_px,
            min_inliers=min_inliers,
            inlier_ratio_thresh=inlier_ratio_thresh,
            return_reasons=True,
        )

        keyframes = thin_keyframes(keyframes)
        keyframe_reasons = {k: keyframe_reasons.get(k, "") for k in keyframes}

        # Persist keyframes only for auto mode (next to processed_frames, which holds only the camera folders)
        with open(os.path.join(dataset_path, "keyframes.txt"), "w") as f:
            for k in keyframes:
                reason = keyframe_reasons.get(k, "")
                f.write(f"{k}\t{reason}\n")

    # open captures
    caps: Dict[str, cv2.VideoCapture] = {cam: cv2.VideoCapture(path) for cam, path in video_files.items()}
    for cam, cap in caps.items():
        if not cap.isOpened():
            raise RuntimeError(f"Cannot open {video_files[cam]}")

    georeferencer = None
    if nav_path:
        from nav_georef import FrameGeoreferencer
        fps_nav: float = caps[ref_cam].get(cv2.CAP_PROP_FPS) or 30.0
        georeferencer = FrameGeoreferencer(nav_path, fps=fps_nav)

    # prepare per-camera folders
    for cam_id in video_files:
        cam_name: str = CAMERA_NAME_MAP.get(cam_id, cam_id)
        os.makedirs(os.path.join(out_folder, f"{cam_id}_{cam_name}"), exist_ok=True)

    frame_no: int = 0
    saved: int = 0
    keyset = set(keyframes) if use_auto else None

    while True:
        frames: Dict[str, npt.NDArray[np.uint8]] = {}
        all_done: bool = True

        for cam, cap in caps.items():
            ret, frame = cap.read()
            if not ret:
                continue
            all_done = False
            # collect frames only if we will save on this frame
            should_save = (frame_no in keyset) if use_auto else (fixed_interval > 0 and (frame_no % fixed_interval == 0))
            if should_save:
                frames[cam] = frame  # frame is ndarray (H,W,3) uint8

        if all_done:
            break

        # Save when we have frames from all 4 cams and the frame is selected
        should_save_now = ((len(frames) == 4) and ((frame_no in keyset) if use_auto else (fixed_interval > 0 and (frame_no % fixed_interval == 0))))
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
                # save final image for texturing: JPG with GPS EXIF when there is navigation, else PNG
                if georeferencer is not None:
                    out_path: str = os.path.join(cam_folder, f"{cam_id}_img_{frame_no}_best.jpg")
                    georeferencer.save_jpg(out_img, out_path, cam_id, frame_no)
                else:
                    out_path = os.path.join(cam_folder, f"{cam_id}_img_{frame_no}_best.png")
                    cv2.imwrite(out_path, out_img)

                print(f"[SAVE] cam={cam_id} frame={frame_no} mode={mode} keyframe_mode={keyframe_mode}")

            saved += 1

        frame_no += 1

    for cap in caps.values():
        cap.release()

    if georeferencer is not None:
        positions_csv = os.path.join(dataset_path, "geotags.csv")
        georeferencer.write_positions(positions_csv)
        print(f"[GEO] {len(georeferencer.rows)} image(s) geotagged (EXIF GPS) in the camera folders; "
              f"positions written to {positions_csv}")
        if georeferencer.outside:
            print(f"[GEO][WARN] {georeferencer.outside} image(s) had no navigation at their time and have no GPS")

        # External georeferencing (Cesium tileset.json) for models built in local coordinates
        from nav_georef import generate_external_georeferencing
        dataset_name = os.path.basename(os.path.normpath(dataset_path))
        generate_external_georeferencing(
            nav_path,
            os.path.join(dataset_path, "external_georeferencing"),
            model_uri=f"{dataset_name}.glb",
        )

    print(f"Done. Saved {saved} keyframe sets to {out_folder}")


# -------- CLI  ---------
def create_parser() -> argparse.ArgumentParser:
    """
    Create and return the ArgumentParser used by this module.
    Exposing this allows other modules (e.g., the GUI) to read default values
    without duplicating them.
    """
    p = argparse.ArgumentParser(description="Main preprocessing: keyframe selection + filters")
    p.add_argument("dataset_path", type=str, help="Folder with the 4 camera videos")

    # defaults live ONLY here
    p.add_argument("--mode", type=str, choices=["clear", "turbid", "raw"], default="raw")
    # keyframe strategy
    p.add_argument("--keyframe-mode", "--keyframe_mode", type=str, choices=["auto", "fixed"], default="auto",
                   help="Keyframe selection strategy: 'auto' (optical flow based) or 'fixed' (every N frames)")
    p.add_argument("--fixed-interval", "--fixed_interval", type=int, default=10,
                   help="If --keyframe-mode fixed, save one frame every N frames (N>0)")
    p.add_argument("--min-disp", type=float, default=150.0)
    p.add_argument("--min-inliers", type=int, default=90)
    p.add_argument("--inlier-ratio-thresh", "--inlier_ratio_thresh", type=float, default=0.7)
    p.add_argument("--nav", type=str, default=None,
                   help="Optional navigation CSV (or folder, *_frames.csv preferred). If given, every saved frame "
                        "of every camera is written as a JPG with GPS EXIF")
    return p


def parse_args() -> argparse.Namespace:
    p = create_parser()
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()

    extract_frames_with_keyframes(
        args.dataset_path,
        mode=args.mode,
        keyframe_mode=args.keyframe_mode,
        fixed_interval=args.fixed_interval,
        min_disp_px=args.min_disp,
        min_inliers=args.min_inliers,
        inlier_ratio_thresh=args.inlier_ratio_thresh,
        nav_path=args.nav,
    )
