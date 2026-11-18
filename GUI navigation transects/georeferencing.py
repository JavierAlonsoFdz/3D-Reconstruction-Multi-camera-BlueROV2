#!/usr/bin/env python3
"""
Crop the 4 multicamera videos of a mission and georeference every frame of the crop.

For a mission recorded with the ROV GUI (videos downloaded with Save Videos, plus its
navigation data folder), this:
  1. finds the frames of cam0 (aux_right) whose time inside the video is in [--start, --end)
     and cuts exactly those frames out of each .mkv, converted to .mp4 (same ffmpeg settings as
     converter.py). Frame k of every .mp4 is frame k of the CSVs below: the 4 cameras are
     frame-synchronised (same start, same frame count), so the same frame numbers are cut;
  2. writes <mission>_<HHMMSS start>_<HHMMSS end>_cam0_frames.csv: ONE ROW PER FRAME of the crop with
     the cam0 position, depth, altitude, ROV attitude, cam0 attitude/quaternion and local position
     at the moment that frame was taken, to geotag any frame you pick later (e.g. EXIF for Zephyr).
     Its Frame column starts at 0 with the first frame of the MP4s, as the GUI Preprocessing counts.

Frame times come from the mission's *_cam0_frames.csv, written by the ROV GUI's Save Videos (the
video's recording date, corrected to the topside clock, plus each frame's presentation time;
checked against the yaw seen in the video to within +-0.05 s). For older missions without it, the
same table is built here from *_cam0_transformed.csv and the frame times read with ffprobe.

Roll/Pitch/Heading are the ROV (body) attitude in degrees (pitch < 0 = nose down, heading
clockwise from north): from *_navigation.csv when present, else recovered from the cam0 attitude
and the camera mount pitch (CAMERA_MOUNT_PITCH_DEG). The preprocessing uses them to place cam1..cam3.

Usage:
    python georeferencing.py --videos videos/<mission> --navigation "navigation data/<mission>" --start 00:00:03 --end 00:00:10
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

CAMERA_NAME_MAP = {"cam0": "aux_right", "cam1": "aux_left", "cam2": "bottom_most", "cam3": "stereo_bottom"}
REFERENCE_CAMERA = "cam0"
NAVIGATION_FILES = {
    "cam0": "_cam0_transformed.csv",  # position of cam0 (offset and mount applied by the ROV GUI)
    "vehicle": "_navigation.csv",  # position of the vehicle (IMU)
}
FRAMES_SUFFIX = "_cam0_frames.csv"  # one row per cam0 video frame, written by the ROV GUI (Save Videos)
FRAME_FIELDS = ("Frame", "SourceFrame", "VideoTime_s", "Date", "Timestamp", "Latitude", "Longitude", "Depth",
                "Roll", "Pitch", "Heading", "Altitude",
                "CamRoll", "CamPitch", "CamHeading", "qx", "qy", "qz", "qw", "North", "East", "Down")
# cam0 mount pitch on the ROV (ROV GUI config.json: cam0.pitch_deg).
CAMERA_MOUNT_PITCH_DEG = -40.0
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
    # ROV (body) attitude, degrees; None if the vehicle navigation CSV is not available
    roll: Optional[float] = None
    pitch: Optional[float] = None
    heading: Optional[float] = None


@dataclass
class Frame:
    """One cam0 video frame and the pose at its capture time (None = no navigation at that moment)."""
    index: int  # frame number in the whole video (presentation order)
    video_time_s: float  # presentation time inside the video
    time: datetime
    values: dict = field(default_factory=dict)  # lat, lon, depth, altitude, roll..heading, cam_*, q*, north..down

    @property
    def covered(self) -> bool:
        return self.values.get("lat") is not None


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


def frame_pts(path: Path) -> list[float]:
    """Presentation time (s) of every video frame, in presentation order (packets only, no decoding)."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts_time",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    )
    return sorted(float(line.split(",")[0]) for line in result.stdout.split() if line.strip() and line != "N/A")


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


def find_optional(path: Path, suffix: str, mission_folder: Optional[str] = None) -> Optional[Path]:
    """The *suffix file in the navigation folder (or next to the given CSV), or None.

    With several (a folder holding many missions), the one inside a folder named like the videos'
    mission folder (mission_folder, e.g. 'mission_0005_multicamera_recording_165448') is taken.
    """
    path = Path(path)
    if path.is_file() and path.name.endswith(suffix):
        return path
    folder = path.parent if path.is_file() else path
    candidates = sorted(folder.rglob(f"*{suffix}"))
    if mission_folder:
        # In a folder of several missions only the videos' own mission counts; a file directly in the
        # chosen folder (the user picked that mission's folder) is accepted whatever its name.
        candidates = [c for c in candidates if c.parent.name == mission_folder or c.parent == folder]
    return candidates[0] if len(candidates) == 1 else None


def mission_name(navigation_csv: Path) -> str:
    """'multicamera_recording_161229_20261020_161229_cam0_transformed.csv' -> 'multicamera_recording_161229'."""
    stem = navigation_csv.name
    for suffix in (*NAVIGATION_FILES.values(), FRAMES_SUFFIX):
        stem = stem.replace(suffix, "")
    return re.sub(r"_\d{8}_\d{6}$", "", stem)


def load_vehicle_attitude(navigation_csv: Path) -> dict[str, tuple[float, float, float]]:
    """{timestamp: (roll, pitch, heading)} of the ROV body, from the *_navigation.csv next to navigation_csv.

    The cam0 CSV holds the camera's attitude (body + mount), so the body attitude comes from the
    vehicle CSV; both are written row by row from the same ATTITUDE messages (same timestamps).
    """
    try:
        vehicle_csv = find_navigation(navigation_csv, "vehicle")
    except FileNotFoundError:
        return {}
    attitude = {}
    with vehicle_csv.open(newline="", encoding="utf-8") as file_obj:
        for row in csv.DictReader(file_obj):
            if row.get("roll_deg") and row.get("pitch_deg") and row.get("heading_deg"):
                attitude[row["timestamp"]] = (float(row["roll_deg"]), float(row["pitch_deg"]), float(row["heading_deg"]))
    return attitude


def load_fixes(navigation_csv: Path) -> list[Fix]:
    attitude = load_vehicle_attitude(navigation_csv)
    fixes = []
    with navigation_csv.open(newline="", encoding="utf-8") as file_obj:
        for row in csv.DictReader(file_obj):
            if row.get("lat_deg") and row.get("lon_deg") and row.get("depth_m"):
                roll, pitch, heading = attitude.get(row["timestamp"], (None, None, None))
                fixes.append(Fix(datetime.fromisoformat(row["timestamp"]), float(row["lat_deg"]),
                                 float(row["lon_deg"]), float(row["depth_m"]), roll, pitch, heading))
    if len(fixes) < 2:
        raise ValueError(f"{navigation_csv.name} has no rows with latitude, longitude and depth")
    return fixes


# ---------------- Rotations (NED, body FRD; same conventions as the ROV GUI exporter) ----------------
def _rot_x(a: float) -> list[list[float]]:
    c, s = math.cos(a), math.sin(a)
    return [[1, 0, 0], [0, c, -s], [0, s, c]]


def _rot_y(a: float) -> list[list[float]]:
    c, s = math.cos(a), math.sin(a)
    return [[c, 0, s], [0, 1, 0], [-s, 0, c]]


def _rot_z(a: float) -> list[list[float]]:
    c, s = math.cos(a), math.sin(a)
    return [[c, -s, 0], [s, c, 0], [0, 0, 1]]


def _mul(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def euler_to_matrix(roll_deg: float, pitch_deg: float, heading_deg: float) -> list[list[float]]:
    return _mul(_rot_z(math.radians(heading_deg)), _mul(_rot_y(math.radians(pitch_deg)), _rot_x(math.radians(roll_deg))))


def matrix_to_euler(r: list[list[float]]) -> tuple[float, float, float]:
    pitch = math.asin(max(-1.0, min(1.0, -r[2][0])))
    roll = math.atan2(r[2][1], r[2][2])
    heading = math.atan2(r[1][0], r[0][0])
    return math.degrees(roll), math.degrees(pitch), math.degrees(heading) % 360.0


def body_from_camera(cam_roll: float, cam_pitch: float, cam_heading: float) -> tuple[float, float, float]:
    """ROV body attitude from the cam0 attitude: camera FRD = body pitched by the mount angle."""
    camera = euler_to_matrix(cam_roll, cam_pitch, cam_heading)
    return matrix_to_euler(_mul(camera, _rot_y(math.radians(-CAMERA_MOUNT_PITCH_DEG))))


# ---------------- Frame table ----------------
def _number(text: Optional[str]) -> Optional[float]:
    return float(text) if text not in (None, "") else None


def _interp_rows(rows: list[dict], times: list[float], when: float, names: dict[str, str],
                 angles: tuple[str, ...] = ()) -> Optional[dict]:
    """Linear interpolation of the named columns at `when` (seconds); None outside the rows."""
    if not times or not times[0] <= when <= times[-1]:
        return None
    import bisect  # noqa: PLC0415
    index = min(bisect.bisect_right(times, when) - 1, len(times) - 2)
    span = times[index + 1] - times[index]
    weight = 0.0 if span <= 0 else (when - times[index]) / span
    out = {}
    for key, column in names.items():
        a, b = _number(rows[index].get(column)), _number(rows[index + 1].get(column))
        if a is None or b is None:
            out[key] = a if b is None else b
        elif key in angles:
            out[key] = (a + weight * ((b - a + 180.0) % 360.0 - 180.0)) % 360.0
        else:
            out[key] = a + weight * (b - a)
    return out


def _attitude_from_navigation(navigation_csv: Optional[Path]) -> tuple[list[dict], list[float]]:
    """(rows, times in epoch s) of *_navigation.csv, for the ROV body attitude; empty if not there."""
    if navigation_csv is None or not navigation_csv.exists():
        return [], []
    with navigation_csv.open(newline="", encoding="utf-8") as file_obj:
        rows = [row for row in csv.DictReader(file_obj) if row.get("heading_deg")]
    return rows, [datetime.fromisoformat(row["timestamp"]).timestamp() for row in rows]


def _complete(frame: Frame, nav_rows: list[dict], nav_times: list[float]) -> None:
    """Add the ROV body attitude: from *_navigation.csv, else from the cam0 attitude and the mount."""
    values = frame.values
    if not frame.covered:
        return
    body = _interp_rows(nav_rows, nav_times, frame.time.timestamp(),
                        {"roll": "roll_deg", "pitch": "pitch_deg", "heading": "heading_deg"}, angles=("heading",))
    if body is None and None not in (values.get("cam_roll"), values.get("cam_pitch"), values.get("cam_heading")):
        body = dict(zip(("roll", "pitch", "heading"),
                        body_from_camera(values["cam_roll"], values["cam_pitch"], values["cam_heading"])))
    if body:
        values.update(body)


def load_frame_table(frames_csv: Path, navigation_csv: Optional[Path]) -> list[Frame]:
    """The ROV GUI's *_cam0_frames.csv as Frame objects."""
    nav_rows, nav_times = _attitude_from_navigation(navigation_csv)
    frames = []
    with frames_csv.open(newline="", encoding="utf-8") as file_obj:
        for row in csv.DictReader(file_obj):
            values = {}
            if row.get("nav_covered") == "true":
                values = {key: _number(row.get(column)) for key, column in (
                    ("lat", "lat_deg"), ("lon", "lon_deg"), ("depth", "depth_m"), ("altitude", "altitude_m"),
                    ("cam_roll", "roll_deg"), ("cam_pitch", "pitch_deg"), ("cam_heading", "heading_deg"),
                    ("qx", "qx"), ("qy", "qy"), ("qz", "qz"), ("qw", "qw"),
                    ("north", "north_m"), ("east", "east_m"), ("down", "down_m"))}
            frame = Frame(int(row["frame"]), float(row["video_time_s"]), datetime.fromisoformat(row["timestamp"]), values)
            _complete(frame, nav_rows, nav_times)
            frames.append(frame)
    return frames


def build_frame_table(video: Video, camera_csv: Path, navigation_csv: Optional[Path]) -> list[Frame]:
    """Same table as the ROV GUI's *_cam0_frames.csv, for missions recorded before it existed."""
    with camera_csv.open(newline="", encoding="utf-8") as file_obj:
        rows = [row for row in csv.DictReader(file_obj) if row.get("lat_deg")]
    times = [datetime.fromisoformat(row["timestamp"]).timestamp() for row in rows]
    nav_rows, nav_times = _attitude_from_navigation(navigation_csv)
    altitude = {row["timestamp"]: row.get("altitude_filtered_m") or row.get("altitude_m") for row in nav_rows}
    for row in rows:
        row["altitude"] = altitude.get(row["timestamp"], "")
    names = {"lat": "lat_deg", "lon": "lon_deg", "depth": "depth_m", "altitude": "altitude",
             "cam_roll": "roll_deg", "cam_pitch": "pitch_deg", "cam_heading": "heading_deg",
             "qx": "qx", "qy": "qy", "qz": "qz", "qw": "qw", "north": "north_m", "east": "east_m", "down": "down_m"}
    frames = []
    pts = frame_pts(video.path)
    first = pts[0] if pts else 0.0
    for index, value in enumerate(pts):
        when = video.start + timedelta(seconds=value)
        values = _interp_rows(rows, times, when.timestamp(), names, angles=("cam_heading",)) or {}
        if values and None not in (values.get("qx"), values.get("qw")):
            norm = math.sqrt(sum(values[k] ** 2 for k in ("qx", "qy", "qz", "qw"))) or 1.0
            for k in ("qx", "qy", "qz", "qw"):
                values[k] /= norm
        frame = Frame(index, value - first, when, values)
        _complete(frame, nav_rows, nav_times)
        frames.append(frame)
    return frames


def frame_table(videos: dict[str, Video], navigation: Path, log: Log = print) -> tuple[list[Frame], str]:
    """(cam0 frames with their poses, where they come from)."""
    mission_folder = videos[REFERENCE_CAMERA].path.parent.name
    navigation_csv = find_optional(navigation, NAVIGATION_FILES["vehicle"], mission_folder)
    frames_csv = find_optional(navigation, FRAMES_SUFFIX, mission_folder)
    if frames_csv is not None:
        return load_frame_table(frames_csv, navigation_csv), frames_csv.name
    camera_csv = find_optional(navigation, NAVIGATION_FILES["cam0"], mission_folder)
    if camera_csv is None:
        raise FileNotFoundError(f"no *{FRAMES_SUFFIX} (or *{NAVIGATION_FILES['cam0']}) for {mission_folder} in {navigation}")
    log(f"no *{FRAMES_SUFFIX} (mission saved before it existed): building it from {camera_csv.name} "
        "and the frame times of the cam0 video")
    return build_frame_table(videos[REFERENCE_CAMERA], camera_csv, navigation_csv), f"{camera_csv.name} + video frame times"


# ---------------- Output ----------------
def _fmt(value: Optional[float], decimals: int) -> str:
    return "" if value is None else f"{value:.{decimals}f}"


def write_frames_csv(path: Path, frames: list[Frame]) -> None:
    with path.open("w", newline="", encoding="utf-8") as file_obj:
        writer = csv.writer(file_obj)
        writer.writerow(FRAME_FIELDS)
        first = frames[0].video_time_s
        for crop_index, frame in enumerate(frames):
            v = frame.values
            writer.writerow([
                crop_index, frame.index, f"{frame.video_time_s - first:.4f}",
                frame.time.strftime("%Y-%m-%d"), frame.time.strftime("%H:%M:%S.%f")[:-3],
                _fmt(v.get("lat"), 8), _fmt(v.get("lon"), 8), _fmt(v.get("depth"), 3),
                _fmt(v.get("roll"), 2), _fmt(v.get("pitch"), 2), _fmt(v.get("heading"), 2),
                _fmt(v.get("altitude"), 3),
                _fmt(v.get("cam_roll"), 2), _fmt(v.get("cam_pitch"), 2), _fmt(v.get("cam_heading"), 2),
                *(_fmt(v.get(k), 6) for k in ("qx", "qy", "qz", "qw")),
                *(_fmt(v.get(k), 3) for k in ("north", "east", "down")),
            ])


def crop_video(video: Video, out_dir: Path, first_pts: float, last_pts: float, expected_frames: int,
               real_start: datetime, cancelled: Callable[[], bool], log: Log) -> Path:
    """Cut the frames whose presentation time is in [first_pts, last_pts] and convert to MP4.

    Fast seek to just before the crop, original timestamps kept (-copyts), then the frames are
    selected by their own time, so exactly those frames are written whatever the keyframes.
    """
    out_path = out_dir / (video.path.stem + ".mp4")
    utc_start = real_start.astimezone(timezone.utc)  # naive local time -> UTC, as MP4 dates are stored
    creation = utc_start.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc_start.microsecond // 1000:03d}Z"
    half_frame = 0.5 / video.fps
    low, high = first_pts - half_frame, last_pts + half_frame
    command = [
        "ffmpeg", "-y", "-v", "error",
        "-ss", f"{max(0.0, first_pts - 3.0):.3f}", "-copyts", "-i", str(video.path),
        "-vf", f"select='between(t\\,{low:.4f}\\,{high:.4f})',setpts=PTS-STARTPTS",
        "-fps_mode", "passthrough", "-an",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
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
    written = len(frame_pts(out_path))
    note = "" if written == expected_frames else f" -- WARNING: {written} frames, expected {expected_frames}"
    log(f"  {out_path.name}: {written} frames, done in {time.monotonic() - started:.0f} s{note}")
    return out_path


def run(videos_dir: Path, navigation: Path, start: str, end: str, out_root: Optional[Path] = None,
        log: Log = print, cancelled: Callable[[], bool] = lambda: False) -> Path:
    """Crop the videos to [start, end) and write the per-frame georeferencing; returns the output folder."""
    require_ffmpeg()
    start_s, end_s = parse_time(start), parse_time(end)
    if end_s <= start_s:
        raise ValueError("--end must be after --start")

    videos = find_videos(Path(videos_dir), log)
    reference = videos[REFERENCE_CAMERA]
    for video in videos.values():
        log(f"{video.camera} {video.path.name}: starts {video.start:%Y-%m-%d %H:%M:%S.%f}"[:-3]
            + f", {video.duration_s:.1f} s at {video.fps:g} fps")
    if end_s > reference.duration_s + 0.5:
        raise ValueError(f"--end {end} is after the end of the video ({format_offset(reference.duration_s)})")
    spread = max(abs((v.start - reference.start).total_seconds()) for v in videos.values())
    if spread > 0.02:
        log(f"warning: the cameras started up to {spread:.3f} s apart; the same frame numbers are cut from each, "
            f"timed by {reference.path.name}")

    frames, source = frame_table(videos, Path(navigation), log)
    selected = [frame for frame in frames if start_s <= frame.video_time_s < end_s]
    if not selected:
        raise ValueError(f"no frames between {start} and {end}")
    uncovered = sum(not frame.covered for frame in selected)
    first, last = selected[0], selected[-1]
    log(f"frames: {source}; crop = frames {first.index}-{last.index} ({len(selected)} frames), "
        f"{first.time:%H:%M:%S.%f}"[:-3] + f" - {last.time:%H:%M:%S.%f}"[:-3] + " (topside clock)")
    if uncovered:
        log(f"warning: {uncovered} frame(s) of the crop have no navigation (before/after the navigation recording): "
            "their position is empty")

    # 'multicamera_recording_161229' -> 'multicamera_recording': the crop times replace the recording start
    navigation_path = Path(navigation)
    mission_folder = reference.path.parent.name
    name_source = (find_optional(navigation_path, FRAMES_SUFFIX, mission_folder)
                   or find_optional(navigation_path, NAVIGATION_FILES["cam0"], mission_folder))
    mission = re.sub(r"_\d{6}$", "", mission_name(name_source))
    name = f"{mission}_{first.time:%H%M%S}_{last.time:%H%M%S}"
    out_dir = Path(out_root or Path(__file__).resolve().parent / "output") / name
    out_dir.mkdir(parents=True, exist_ok=True)

    frames_path = out_dir / f"{name}_cam0_frames.csv"
    write_frames_csv(frames_path, selected)
    for label, frame in (("first", first), ("last", last)):
        v = frame.values
        if frame.covered:
            log(f"  {label} frame: {v['lat']:.8f}, {v['lon']:.8f}, depth {v['depth']:.3f} m"
                + ("" if v.get("heading") is None else
                   f"; ROV roll {v['roll']:.1f}, pitch {v['pitch']:.1f}, heading {v['heading']:.1f} deg"))
    log(f"wrote {frames_path.name} ({len(selected)} rows, one per frame; Frame k = frame k of the MP4s)")

    log(f"converting {len(videos)} videos to MP4 (frames {first.index}-{last.index})...")
    for video in videos.values():
        # The same frame numbers in every camera (frame-synchronised), found by each video's own frame times.
        times = frame_pts(video.path)
        if last.index >= len(times):
            log(f"warning: {video.path.name} has only {len(times)} frames; skipped")
            continue
        crop_video(video, out_dir, times[first.index], times[last.index], len(selected), first.time, cancelled, log)
    log(f"done: {out_dir}")
    return out_dir


def _clock(moment: datetime) -> str:
    return moment.strftime("%H:%M:%S.%f")[:-3]


def navigation_span(navigation: Path, mission_folder: str) -> tuple[datetime, datetime, bool, str]:
    """(first, last navigation time, whether the last one is exact, the file it comes from).

    From *_cam0_transformed.csv / *_navigation.csv when present (exact). From *_cam0_frames.csv only:
    the start is exact (frame time - its navigation time t_s), the end is the last frame with a
    pose (within one frame of the real end, or the video end if every frame has one).
    """
    for suffix in (NAVIGATION_FILES["cam0"], NAVIGATION_FILES["vehicle"]):
        path = find_optional(navigation, suffix, mission_folder)
        if path is not None:
            with path.open(newline="", encoding="utf-8") as file_obj:
                stamps = [row["timestamp"] for row in csv.DictReader(file_obj) if row.get("timestamp")]
            return datetime.fromisoformat(stamps[0]), datetime.fromisoformat(stamps[-1]), True, path.name
    frames_csv = find_optional(navigation, FRAMES_SUFFIX, mission_folder)
    if frames_csv is None:
        raise FileNotFoundError(f"no *{FRAMES_SUFFIX} or *{NAVIGATION_FILES['cam0']} for {mission_folder} in {navigation}")
    with frames_csv.open(newline="", encoding="utf-8") as file_obj:
        rows = list(csv.DictReader(file_obj))
    covered = [row for row in rows if row.get("nav_covered") == "true"]
    if not covered:
        raise ValueError(f"{frames_csv.name}: no frame has navigation")
    first = covered[0]
    start = datetime.fromisoformat(first["timestamp"]) - timedelta(seconds=float(first["t_s"]))
    return start, datetime.fromisoformat(covered[-1]["timestamp"]), covered[-1] is not rows[-1], frames_csv.name


def describe(videos_dir: Path, navigation: Path) -> str:
    """Summary for the GUI: start and end of the video and of the navigation, and how they overlap."""
    videos = find_videos(Path(videos_dir), log=lambda _m: None)
    ref = videos[REFERENCE_CAMERA]
    video_end = ref.start + timedelta(seconds=ref.duration_s)
    text = (f"Video ({len(videos)} cameras): {ref.start:%Y-%m-%d} {_clock(ref.start)} → {_clock(video_end)} · "
            f"{format_offset(ref.duration_s)} ({ref.duration_s:.1f} s) at {ref.fps:g} fps")
    try:
        nav_start, nav_end, end_exact, source = navigation_span(Path(navigation), ref.path.parent.name)
        text += f"\nNavigation ({source}): {_clock(nav_start)} → {'' if end_exact else '≥ '}{_clock(nav_end)}"
        lead = (ref.start - nav_start).total_seconds()
        if lead >= 0:
            start_note = f"navigation starts {lead:.3f} s before the video ✓ (the first frame has a pose)"
        else:
            start_note = (f"navigation starts {-lead:.3f} s AFTER the video: the first ~{round(-lead * ref.fps)} frames "
                          f"(video time 00:00:00 - {format_offset(-lead)}) have no pose")
        tail = (video_end - nav_end).total_seconds()
        if tail <= 0 or not end_exact and tail < 1.0 / ref.fps:
            end_note = (f"navigation ends {-tail:.3f} s after the video ✓ (the last frame has a pose)" if end_exact
                        else "navigation reaches the last frame ✓")
        else:
            end_note = (f"navigation ends {tail:.3f} s BEFORE the video: the last ~{round(tail * ref.fps)} frames "
                        f"(video time {format_offset(ref.duration_s - tail)} - {format_offset(ref.duration_s)}) have no pose")
        text += f"\nStart: {start_note}\nEnd: {end_note}"
    except Exception as error:  # noqa: BLE001
        text += f"\nNavigation: {error}"
    return text


def main() -> None:
    parser = argparse.ArgumentParser(description="Crop the multicamera videos and georeference every frame of the crop.")
    parser.add_argument("--videos", required=True, help="Folder with the 4 .mkv videos of the mission")
    parser.add_argument("--navigation", required=True, help="Navigation data folder of the mission (or one of its CSVs)")
    parser.add_argument("--start", required=True, help="Crop start inside the video, HH:MM:SS")
    parser.add_argument("--end", required=True, help="Crop end inside the video, HH:MM:SS")
    parser.add_argument("--out", help="Output root folder (default: output/ next to this script)")
    args = parser.parse_args()
    run(Path(args.videos), Path(args.navigation), args.start, args.end, Path(args.out) if args.out else None)


if __name__ == "__main__":
    main()
