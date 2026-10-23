#!/usr/bin/env python3
"""
Crop the 4 multicamera videos of a mission and georeference the crop.

For a mission recorded with the ROV GUI (videos downloaded with Save Videos, plus its
navigation data folder), this:
  1. cuts --start .. --end (times inside the video) out of each .mkv and converts it to .mp4
     (same ffmpeg settings as converter.py);
  2. writes <mission>_<HHMMSS start>_<HHMMSS end>.csv with three rows:
         Date, Timestamp, Latitude, Longitude, Depth
         <position at the first frame of the crop>
         <position at the last frame of the crop>
     interpolated in time from the cam0-referenced navigation (cam0 = aux_right).

Time base: the topside (laptop) clock, which is what the navigation CSVs use. The ROV GUI writes
the real recording start into each .mkv when it downloads it; recording_time.txt is the fallback.

Usage:
    python georeferencing.py --videos videos --navigation "navigation data" --start 00:00:05 --end 00:00:25
"""

from __future__ import annotations

import argparse
import bisect
import csv
import json
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

CAMERA_NAME_MAP = {"cam0": "aux_right", "cam1": "aux_left", "cam2": "bottom_most", "cam3": "stereo_bottom"}
REFERENCE_CAMERA = "cam0"
NAVIGATION_FILES = {
    "cam0": "_cam0_transformed.csv",  # position of cam0 (offset and mount applied by the ROV GUI)
    "vehicle": "_navigation.csv",  # position of the vehicle (IMU)
}
CSV_FIELDS = ("Date", "Timestamp", "Latitude", "Longitude", "Depth")
# A video date this far from the click time was not corrected to the topside clock.
UNCORRECTED_DATE_S = 60.0

Log = Callable[[str], None]


@dataclass
class Video:
    camera: str
    path: Path
    start: datetime  # real (topside clock) time of the first frame, local time
    duration_s: float
    fps: float


@dataclass
class Fix:
    time: datetime
    lat: float
    lon: float
    depth: float


def parse_time(text: str) -> float:
    """'00:03:00', '3:00', '00:00:05.5' or '125' -> seconds."""
    parts = text.strip().split(":")
    if not 1 <= len(parts) <= 3:
        raise ValueError(f"not a time: {text!r} (use HH:MM:SS)")
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + float(part)
    if seconds < 0:
        raise ValueError(f"negative time: {text!r}")
    return seconds


def format_offset(seconds: float) -> str:
    whole = int(seconds)
    text = f"{whole // 3600:02d}:{whole % 3600 // 60:02d}:{whole % 60:02d}"
    fraction = seconds - whole
    return text + (f".{round(fraction * 1000):03d}" if fraction > 0.0005 else "")


def require_ffmpeg() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            raise RuntimeError(f"{tool} is not in PATH. Install ffmpeg and add its bin folder to PATH.")


def read_recording_time(*folders: Path) -> dict[str, str]:
    """Fields of the recording_time.txt the ROV GUI leaves with the videos, if there is one."""
    for folder in folders:
        note = folder / "recording_time.txt"
        if note.exists():
            fields = {}
            for line in note.read_text(encoding="utf-8", errors="replace").splitlines():
                key, _, value = line.partition(":")
                if value:
                    fields[key.strip()] = value.strip()
            return fields
    return {}


def probe(path: Path) -> tuple[Optional[datetime], float, float]:
    """(recording date as local time or None, duration s, fps) from ffprobe."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "format=duration:format_tags=creation_time:stream=r_frame_rate", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )
    info = json.loads(result.stdout)
    fmt = info.get("format", {})
    created = (fmt.get("tags") or {}).get("creation_time")
    start = datetime.fromisoformat(created.replace("Z", "+00:00")).astimezone().replace(tzinfo=None) if created else None
    numerator, _, denominator = info["streams"][0]["r_frame_rate"].partition("/")
    fps = float(numerator) / float(denominator or 1)
    return start, float(fmt["duration"]), fps


def find_videos(folder: Path, log: Log = print) -> dict[str, Video]:
    """The 4 camera videos in folder, keyed cam0..cam3, with their real start times."""
    folder = Path(folder)
    if not folder.is_dir():
        raise FileNotFoundError(f"video folder not found: {folder}")
    note = read_recording_time(folder, folder.parent)
    clicked = datetime.fromisoformat(note["Recording clicked (topside clock)"]) if "Recording clicked (topside clock)" in note else None
    videos: dict[str, Video] = {}
    for camera, token in CAMERA_NAME_MAP.items():
        matches = sorted(p for p in folder.iterdir() if p.suffix.lower() in (".mkv", ".mp4") and token in p.stem)
        if not matches:
            continue
        path = matches[0]
        start, duration, fps = probe(path)
        if start is None or (clicked is not None and abs((start - clicked).total_seconds()) > UNCORRECTED_DATE_S):
            fallback = note.get("Video start (topside clock)")
            if not fallback:
                raise ValueError(f"{path.name}: the video date is missing or not corrected to the topside clock, "
                                 "and there is no recording_time.txt. Download the videos with the ROV GUI (Save Videos).")
            log(f"{path.name}: video date not usable ({start}); using recording_time.txt ({fallback}, 1 s resolution)")
            start = datetime.fromisoformat(fallback)
        videos[camera] = Video(camera, path, start, duration, fps)
    if REFERENCE_CAMERA not in videos:
        raise FileNotFoundError(f"no {CAMERA_NAME_MAP[REFERENCE_CAMERA]} video ({REFERENCE_CAMERA}) in {folder}")
    return videos


def find_navigation(path: Path, position: str = "cam0") -> Path:
    """The navigation CSV for `position` ('cam0' or 'vehicle'), given the CSV itself or its folder."""
    path = Path(path)
    suffix = NAVIGATION_FILES[position]
    if path.is_file():
        if not path.name.endswith(suffix):
            candidates = sorted(path.parent.glob(f"*{suffix}"))
            if not candidates:
                raise FileNotFoundError(f"no *{suffix} next to {path}")
            return candidates[0]
        return path
    candidates = sorted(Path(path).rglob(f"*{suffix}"))
    if len(candidates) != 1:
        raise FileNotFoundError(f"expected one *{suffix} in {path}, found {len(candidates)}")
    return candidates[0]


def mission_name(navigation_csv: Path) -> str:
    """'multicamera_recording_161229_20261020_161229_cam0_transformed.csv' -> 'multicamera_recording_161229'."""
    stem = navigation_csv.name
    for suffix in NAVIGATION_FILES.values():
        stem = stem.replace(suffix, "")
    return re.sub(r"_\d{8}_\d{6}$", "", stem)


def load_fixes(navigation_csv: Path) -> list[Fix]:
    fixes = []
    with navigation_csv.open(newline="", encoding="utf-8") as file_obj:
        for row in csv.DictReader(file_obj):
            if row.get("lat_deg") and row.get("lon_deg") and row.get("depth_m"):
                fixes.append(Fix(datetime.fromisoformat(row["timestamp"]), float(row["lat_deg"]),
                                 float(row["lon_deg"]), float(row["depth_m"])))
    if len(fixes) < 2:
        raise ValueError(f"{navigation_csv.name} has no rows with latitude, longitude and depth")
    return fixes


def position_at(fixes: list[Fix], when: datetime) -> Fix:
    """Linear interpolation of the navigation at `when` (must be inside the recording)."""
    times = [fix.time for fix in fixes]
    if not times[0] <= when <= times[-1]:
        raise ValueError(f"{when:%H:%M:%S.%f}"[:-3] + f" is outside the navigation data "
                         f"({times[0]:%H:%M:%S} - {times[-1]:%H:%M:%S})")
    index = bisect.bisect_right(times, when)
    if index >= len(fixes):
        return fixes[-1]
    before, after = fixes[index - 1], fixes[index]
    span = (after.time - before.time).total_seconds()
    weight = 0.0 if span <= 0 else (when - before.time).total_seconds() / span
    return Fix(when, before.lat + weight * (after.lat - before.lat), before.lon + weight * (after.lon - before.lon),
               before.depth + weight * (after.depth - before.depth))


def write_csv(path: Path, start: Fix, end: Fix) -> None:
    with path.open("w", newline="", encoding="utf-8") as file_obj:
        writer = csv.writer(file_obj)
        writer.writerow(CSV_FIELDS)
        for fix in (start, end):
            writer.writerow([fix.time.strftime("%Y-%m-%d"), fix.time.strftime("%H:%M:%S.%f")[:-3],
                             f"{fix.lat:.8f}", f"{fix.lon:.8f}", f"{fix.depth:.3f}"])


def crop_video(video: Video, out_dir: Path, start_s: float, end_s: float, real_start: datetime,
               cancelled: Callable[[], bool], log: Log) -> Path:
    """Cut start_s..end_s and convert to MP4 (converter.py settings); the MP4 carries the crop's real start."""
    out_path = out_dir / (video.path.stem + ".mp4")
    utc_start = real_start.astimezone(timezone.utc)  # naive local time -> UTC, as MP4 dates are stored
    creation = utc_start.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc_start.microsecond // 1000:03d}Z"
    command = [
        "ffmpeg", "-y", "-v", "error",
        "-i", str(video.path),
        "-ss", f"{start_s:.3f}", "-to", f"{end_s:.3f}",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-metadata", f"creation_time={creation}",
        str(out_path),
    ]
    started = time.monotonic()
    process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    while process.poll() is None:
        if cancelled():
            process.terminate()
            process.wait()
            out_path.unlink(missing_ok=True)
            raise RuntimeError("cancelled")
        time.sleep(0.2)
    if process.returncode != 0:
        raise RuntimeError(f"ffmpeg failed on {video.path.name}: {process.stderr.read().strip()[-500:]}")
    log(f"  {out_path.name} done in {time.monotonic() - started:.0f} s")
    return out_path


def run(videos_dir: Path, navigation: Path, start: str, end: str, out_root: Optional[Path] = None,
        position: str = "cam0", log: Log = print, cancelled: Callable[[], bool] = lambda: False) -> Path:
    """Crop the videos and write the georeferencing CSV; returns the output folder."""
    require_ffmpeg()
    start_s, end_s = parse_time(start), parse_time(end)
    if end_s <= start_s:
        raise ValueError("--end must be after --start")

    videos = find_videos(Path(videos_dir), log)
    reference = videos[REFERENCE_CAMERA]
    for video in videos.values():
        log(f"{video.camera} {video.path.name}: starts {video.start:%Y-%m-%d %H:%M:%S.%f}"[:-3]
            + f", {video.duration_s:.1f} s at {video.fps:g} fps")
    if end_s > reference.duration_s:
        raise ValueError(f"--end {end} is after the end of the video ({format_offset(reference.duration_s)})")
    spread = max(abs((v.start - reference.start).total_seconds()) for v in videos.values())
    if spread > 0.5:
        log(f"warning: the cameras started up to {spread:.2f} s apart; times follow {reference.path.name}")

    navigation_csv = find_navigation(Path(navigation), position)
    fixes = load_fixes(navigation_csv)
    log(f"navigation: {navigation_csv.name}, {fixes[0].time:%H:%M:%S} - {fixes[-1].time:%H:%M:%S}, {len(fixes)} rows")

    real_start = reference.start + timedelta(seconds=start_s)
    real_end = reference.start + timedelta(seconds=end_s)
    first, last = position_at(fixes, real_start), position_at(fixes, real_end)

    mission = mission_name(navigation_csv)
    name = f"{mission}_{real_start:%H%M%S}_{real_end:%H%M%S}"
    out_dir = Path(out_root or Path(__file__).resolve().parent / "output") / name
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"{name}.csv"
    write_csv(csv_path, first, last)
    log(f"crop {format_offset(start_s)} - {format_offset(end_s)} of the video = "
        f"{real_start:%H:%M:%S.%f}"[:-3] + f" - {real_end:%H:%M:%S.%f}"[:-3] + " (topside clock)")
    log(f"  start: {first.lat:.8f}, {first.lon:.8f}, depth {first.depth:.3f} m")
    log(f"  end:   {last.lat:.8f}, {last.lon:.8f}, depth {last.depth:.3f} m")
    log(f"wrote {csv_path}")

    log(f"converting {len(videos)} videos to MP4...")
    for video in videos.values():
        crop_video(video, out_dir, start_s, end_s, video.start + timedelta(seconds=start_s), cancelled, log)
    log(f"done: {out_dir}")
    return out_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="Crop the multicamera videos and georeference the crop.")
    parser.add_argument("--videos", required=True, help="Folder with the 4 .mkv videos of the mission")
    parser.add_argument("--navigation", required=True, help="Navigation data folder of the mission (or its CSV)")
    parser.add_argument("--start", required=True, help="Crop start inside the video, HH:MM:SS")
    parser.add_argument("--end", required=True, help="Crop end inside the video, HH:MM:SS")
    parser.add_argument("--out", help="Output root folder (default: output/ next to this script)")
    parser.add_argument("--position", choices=sorted(NAVIGATION_FILES), default="cam0",
                        help="Position to report: cam0 (camera, default) or vehicle")
    args = parser.parse_args()
    run(Path(args.videos), Path(args.navigation), args.start, args.end,
        Path(args.out) if args.out else None, args.position)


if __name__ == "__main__":
    main()
