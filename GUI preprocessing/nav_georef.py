#!/usr/bin/env python3
"""
Georeference extracted frames using the navigation CSV recorded with the videos.

Each extracted frame number n is mapped to an absolute time
    t(n) = t_start + n / fps
where t_start is the first timestamp in the navigation CSV (the video and the
navigation log start together). Latitude, longitude and depth are linearly
interpolated from the navigation samples at t(n) and written as EXIF GPS tags
into a JPG copy of the frame (Pillow, no external tools needed).

It also writes a Cesium tileset.json for models built in local coordinates.

Expected CSV columns (case-insensitive): Date, Timestamp, Latitude, Longitude, Depth
"""

from __future__ import annotations
import csv
import glob
import os
import shutil
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
from PIL import Image, TiffImagePlugin


# ---------------- Navigation CSV ----------------
def resolve_nav_csv(nav_path: str) -> str:
    """Accept a CSV file or a folder. In a folder, prefer '*_frames.csv' (one row per frame, from the
    GUI Navigation Transects), then '*_all_timestamps.csv'."""
    if os.path.isfile(nav_path):
        return nav_path
    if os.path.isdir(nav_path):
        dense = (sorted(glob.glob(os.path.join(nav_path, "*_frames.csv")))
                 or sorted(glob.glob(os.path.join(nav_path, "*all_timestamps*.csv"))))
        if dense:
            return dense[0]
        any_csv = sorted(glob.glob(os.path.join(nav_path, "*.csv")))
        if any_csv:
            return any_csv[0]
    raise FileNotFoundError(f"No navigation CSV found at: {nav_path}")


def _parse_time(date_s: str, time_s: str) -> datetime:
    s = f"{date_s.strip()} {time_s.strip()}"
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M:%S.%f", "%d/%m/%Y %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise ValueError(f"Unrecognized date/time: '{s}'")


def load_navigation(csv_path: str) -> Tuple[datetime, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Return (t0, t_sec, lat, lon, depth), with t_sec relative to the first valid row, sorted by time."""
    times: List[datetime] = []
    lats: List[float] = []
    lons: List[float] = []
    depths: List[float] = []

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError("Navigation CSV has no headers.")
        cols = {h.strip().lower(): h for h in reader.fieldnames}

        def col(*names: str) -> Optional[str]:
            for n in names:
                if n in cols:
                    return cols[n]
            return None

        c_date, c_time = col("date"), col("timestamp", "time")
        c_lat, c_lon = col("latitude", "lat"), col("longitude", "lon", "long")
        c_depth = col("depth", "altitude", "z")
        if None in (c_date, c_time, c_lat, c_lon, c_depth):
            raise ValueError("Navigation CSV needs Date, Timestamp, Latitude, Longitude and Depth columns.")

        first_time: Optional[datetime] = None
        for row in reader:
            try:
                t = _parse_time(row[c_date], row[c_time])  # type: ignore[index]
            except (ValueError, TypeError, KeyError):
                continue
            # Frame 0 is the first row, even without a position (a *_frames.csv from the GUI
            # Navigation Transects has one row per frame, empty where there is no navigation).
            if first_time is None or t < first_time:
                first_time = t
            try:
                la, lo, de = float(row[c_lat]), float(row[c_lon]), float(row[c_depth])  # type: ignore[index]
            except (ValueError, TypeError, KeyError):
                continue
            times.append(t)
            lats.append(la)
            lons.append(lo)
            depths.append(de)

    if not times:
        raise ValueError("No valid rows in navigation CSV.")

    order = np.argsort(np.array([t.timestamp() for t in times]))
    t0 = first_time if first_time is not None else times[order[0]]
    t_sec = np.array([(times[i] - t0).total_seconds() for i in order], dtype=float)
    return (
        t0,
        t_sec,
        np.array(lats, dtype=float)[order],
        np.array(lons, dtype=float)[order],
        np.array(depths, dtype=float)[order],
    )


def load_attitude(csv_path: str, t0: datetime) -> Optional[Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]]:
    """
    ROV (body) attitude from the optional Roll, Pitch, Heading columns (degrees) written by the
    navigation-transects GUI: (t_sec relative to t0, roll, pitch, heading), sorted by time.
    None if the CSV has no attitude (older CSVs).
    """
    times: List[float] = []
    rolls: List[float] = []
    pitches: List[float] = []
    headings: List[float] = []
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        cols = {h.strip().lower(): h for h in (reader.fieldnames or [])}
        c_date, c_time = cols.get("date"), cols.get("timestamp") or cols.get("time")
        c_roll, c_pitch, c_head = cols.get("roll"), cols.get("pitch"), cols.get("heading")
        if None in (c_date, c_time, c_roll, c_pitch, c_head):
            return None
        for row in reader:
            try:
                t = _parse_time(row[c_date], row[c_time])  # type: ignore[index]
                r, p, h = float(row[c_roll]), float(row[c_pitch]), float(row[c_head])  # type: ignore[index]
            except (ValueError, TypeError, KeyError):
                continue
            times.append((t - t0).total_seconds())
            rolls.append(r)
            pitches.append(p)
            headings.append(h)
    if not times:
        return None
    order = np.argsort(times)
    return (np.array(times)[order], np.array(rolls)[order], np.array(pitches)[order], np.array(headings)[order])


def attitude_at(att: Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray], ts: float) -> Tuple[float, float, float]:
    """(roll, pitch, heading) in degrees at ts; heading interpolated the short way round (359 -> 1)."""
    t, roll, pitch, heading = att
    unwrapped = np.degrees(np.unwrap(np.radians(heading)))
    return (float(np.interp(ts, t, roll)), float(np.interp(ts, t, pitch)),
            float(np.interp(ts, t, unwrapped)) % 360.0)


def rotation_ned_body(roll_deg: float, pitch_deg: float, heading_deg: float) -> np.ndarray:
    """Body FRD -> local NED (Z-Y-X: heading, pitch, roll), same convention as the ROV GUI exporter."""
    r, p, h = np.radians([roll_deg, pitch_deg, heading_deg])
    rz = np.array([[np.cos(h), -np.sin(h), 0], [np.sin(h), np.cos(h), 0], [0, 0, 1]])
    ry = np.array([[np.cos(p), 0, np.sin(p)], [0, 1, 0], [-np.sin(p), 0, np.cos(p)]])
    rx = np.array([[1, 0, 0], [0, np.cos(r), -np.sin(r)], [0, np.sin(r), np.cos(r)]])
    return rz @ ry @ rx


# ---------------- EXIF writing ----------------
def _deg_to_dms(value: float) -> Tuple[TiffImagePlugin.IFDRational, ...]:
    value = abs(value)
    d = int(value)
    m_full = (value - d) * 60.0
    m = int(m_full)
    s = (m_full - m) * 60.0
    R = TiffImagePlugin.IFDRational
    return (R(d, 1), R(m, 1), R(int(round(s * 1_000_000)), 1_000_000))


def _save_jpg_with_gps(src_png: str, dst_jpg: str, lat: float, lon: float, depth: float, when: datetime) -> None:
    """Convert PNG -> JPG and embed GPS + capture time via Pillow (no exiftool needed)."""
    R = TiffImagePlugin.IFDRational
    exif = Image.Exif()

    # Capture time (helps Zephyr / other tools keep frame order)
    exif_ifd = exif.get_ifd(0x8769)
    exif_ifd[0x9003] = when.strftime("%Y:%m:%d %H:%M:%S")  # DateTimeOriginal
    exif_ifd[0x9291] = f"{when.microsecond // 1000:03d}"  # SubSecTimeOriginal (ms)

    gps = exif.get_ifd(0x8825)
    gps[0] = b"\x02\x03\x00\x00"  # GPSVersionID
    gps[1] = "N" if lat >= 0 else "S"
    gps[2] = _deg_to_dms(lat)
    gps[3] = "E" if lon >= 0 else "W"
    gps[4] = _deg_to_dms(lon)
    gps[5] = b"\x01"  # 1 = below sea level
    gps[6] = R(int(round(abs(depth) * 1000)), 1000)
    gps[7] = (R(when.hour, 1), R(when.minute, 1), R(int(round((when.second + when.microsecond / 1e6) * 1000)), 1000))
    gps[29] = when.strftime("%Y:%m:%d")

    with Image.open(src_png) as img:
        img.convert("RGB").save(dst_jpg, "JPEG", quality=95, exif=exif)


# ---------------- Main entry ----------------
def georeference_frames(
    frame_paths: Dict[int, str],
    nav_path: str,
    *,
    fps: float = 30.0,
    out_dir: str,
    prefix: str = "cam0",
) -> int:
    """
    Georeference every frame in `frame_paths` ({frame_no: png_path}) and write JPGs into out_dir.
    Also writes <prefix>_geo_positions.csv (interpolated values) in the parent of out_dir (processed_frames).
    Returns number of geotagged images.
    """
    csv_path = resolve_nav_csv(nav_path)
    t0, t_sec, lat, lon, depth = load_navigation(csv_path)
    print(f"[GEO] Navigation: {csv_path} ({len(t_sec)} samples, start {t0.time()}, span {t_sec[-1]:.2f}s)")

    os.makedirs(out_dir, exist_ok=True)

    rows = []
    out_of_range = 0
    for frame_no in sorted(frame_paths):
        src = frame_paths[frame_no]
        ts = frame_no / fps
        if ts < t_sec[0] or ts > t_sec[-1]:
            out_of_range += 1  # np.interp clamps to the nearest end value
        la = float(np.interp(ts, t_sec, lat))
        lo = float(np.interp(ts, t_sec, lon))
        de = float(np.interp(ts, t_sec, depth))
        when = t0 + timedelta(seconds=ts)

        dst = os.path.join(out_dir, os.path.splitext(os.path.basename(src))[0] + ".jpg")
        _save_jpg_with_gps(src, dst, la, lo, de, when)

        rows.append((os.path.basename(dst), frame_no, when.strftime("%H:%M:%S.%f")[:-3], la, lo, de))
        print(f"[GEO] frame={frame_no} t={when.strftime('%H:%M:%S.%f')[:-3]} lat={la:.8f} lon={lo:.8f} depth={de:.3f}")

    positions_csv = os.path.join(os.path.dirname(os.path.normpath(out_dir)), f"{prefix}_geo_positions.csv")
    with open(positions_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Image", "Frame", "Timestamp", "Latitude", "Longitude", "Depth"])
        for r in rows:
            w.writerow([r[0], r[1], r[2], f"{r[3]:.8f}", f"{r[4]:.8f}", f"{r[5]:.3f}"])

    if out_of_range:
        print(f"[GEO][WARN] {out_of_range} frame(s) fall outside the navigation time range (position clamped).")
    print(f"[GEO] Geotagged {len(rows)} image(s) into {out_dir}")
    return len(rows)


# ---------------- First/last frame of every camera (rig lever arms) ----------------
# Rig geometry: see Calibration/3DF Zephyr/README.md ("Rig Geometry", Kalibr: 21.8 x 10.9 cm).
# Camera positions relative to cam0 (aux_right) measured ON THE RIG PLATE, metres:
# (right, down_along_plate). Looking forward from the ROV.
RIG_OFFSETS: Dict[str, Tuple[float, float]] = {
    "cam1": (-0.22, 0.0),   # aux_left: same row, 22 cm to the left
    "cam2": (0.0, 0.11),    # bottom_most: 11 cm below cam0 on the plate
    "cam3": (-0.22, 0.11),  # stereo_bottom: 22 cm left + 11 cm below (right under cam1)
}
# The rig is pitched RIG_PITCH_DEG downward RELATIVE TO THE ROV BODY (cameras look forward-down),
# so the plate's "down" direction is tilted: 11 cm along the plate (hypotenuse) =
# 11*cos(pitch) cm down + 11*sin(pitch) cm backwards, in the ROV body frame.
# When the navigation CSV carries the ROV attitude (Roll, Pitch, Heading columns), these body
# offsets are rotated with it, so an extra ROV tilt (e.g. "mount tilt down", ~20 deg nose down)
# is taken into account; otherwise the ROV is assumed level.
RIG_PITCH_DEG: float = 40.0


def rig_to_body_offsets(rig_offsets: Dict[str, Tuple[float, float]],
                        pitch_deg: float) -> Dict[str, Tuple[float, float, float]]:
    """Convert plate offsets (right, down_along_plate) to ROV body offsets (right, forward, up)."""
    p = np.radians(pitch_deg)
    out: Dict[str, Tuple[float, float, float]] = {}
    for cam, (right, down) in rig_offsets.items():
        out[cam] = (right, -down * float(np.sin(p)), -down * float(np.cos(p)))
    return out


OTHER_CAM_OFFSETS: Dict[str, Tuple[float, float, float]] = rig_to_body_offsets(RIG_OFFSETS, RIG_PITCH_DEG)


def estimate_heading_deg(t_sec: np.ndarray, lat: np.ndarray, lon: np.ndarray, ts: float,
                         min_dist_m: float = 0.5) -> float:
    """
    Estimate ROV heading (deg, clockwise from North) at time ts from the navigation
    track direction: displacement from ts to the first LATER sample at least min_dist_m
    away. If the ROV does not move that far after ts (e.g. last frame), use the
    displacement from the last EARLIER sample at least min_dist_m away up to ts.
    Falls back to the whole track (start -> end) if the ROV barely moves.
    """
    lat0 = float(np.interp(ts, t_sec, lat))
    lon0 = float(np.interp(ts, t_sec, lon))
    m_lat, m_lon = _metres_per_degree(lat0)
    de = (lon - lon0) * m_lon
    dn = (lat - lat0) * m_lat
    dist = np.hypot(de, dn)

    ahead = np.where((t_sec >= ts) & (dist >= min_dist_m))[0]
    if ahead.size:
        i = int(ahead[0])
        e, n = de[i], dn[i]            # current -> future
    else:
        behind = np.where((t_sec <= ts) & (dist >= min_dist_m))[0]
        if behind.size:
            i = int(behind[-1])
            e, n = -de[i], -dn[i]      # past -> current
        else:
            e = (lon[-1] - lon[0]) * m_lon
            n = (lat[-1] - lat[0]) * m_lat
    if np.hypot(e, n) < 1e-6:
        raise ValueError("Cannot estimate heading: the navigation track does not move.")
    return float(np.degrees(np.arctan2(e, n)) % 360.0)


def _metres_per_degree(lat_deg: float) -> Tuple[float, float]:
    """(metres per degree latitude, metres per degree longitude) on WGS84 at lat_deg."""
    a, f = 6378137.0, 1.0 / 298.257223563
    e2 = f * (2.0 - f)
    phi = np.radians(lat_deg)
    s2 = np.sin(phi) ** 2
    m = a * (1.0 - e2) / (1.0 - e2 * s2) ** 1.5  # meridional radius
    n = a / np.sqrt(1.0 - e2 * s2)               # prime vertical radius
    return float(np.radians(1.0) * m), float(np.radians(1.0) * n * np.cos(phi))


def georeference_first_last_inplace(
    cam_frames: Dict[str, List[Tuple[int, str]]],
    nav_path: str,
    *,
    fps: float = 30.0,
    processed_dir: str,
    original_dir: str,
    offsets: Optional[Dict[str, Tuple[float, float, float]]] = None,
    heading_deg: Optional[float] = None,
) -> int:
    """
    Georeference the first and last frame of every camera IN PLACE:
      - the original PNG is moved to `original_dir` (untouched, no EXIF; use these
        if you want to build the model in local coordinates),
      - in the camera folder it is replaced by a JPG with the same name + GPS EXIF.
    Position = cam0 position at that frame (interpolated from the navigation CSV)
    + the fixed lever-arm offset of each camera (cam0 = 0), rotated by the ROV attitude:
      - if the CSV has Roll, Pitch, Heading (ROV body attitude, written by the navigation-transects
        GUI), the full roll/pitch/heading of that frame is used (correct with "mount tilt down");
      - otherwise the ROV is assumed level and the heading is estimated from the track.
    `heading_deg`, if given, overrides the heading in both cases.
    cam_frames: {cam_id: [(frame_no, png_path), ...]}.
    Writes first_last_geo_positions.csv into processed_dir.
    """
    offsets = dict(offsets or OTHER_CAM_OFFSETS)
    offsets.setdefault("cam0", (0.0, 0.0, 0.0))
    csv_path = resolve_nav_csv(nav_path)
    t0, t_sec, lat, lon, depth = load_navigation(csv_path)
    attitude = load_attitude(csv_path, t0)
    if attitude is None:
        print("[GEO-FL][WARN] The navigation CSV has no Roll/Pitch/Heading columns: assuming the ROV is level "
              "and estimating the heading from the track. Re-export the transect with the navigation-transects GUI "
              "to use the real ROV attitude.")
    else:
        print(f"[GEO-FL] Using the real ROV attitude from {os.path.basename(csv_path)} ({len(attitude[0])} samples).")
    os.makedirs(original_dir, exist_ok=True)

    jobs = sorted({(cam_id, fno, src) for cam_id, lst in cam_frames.items() for fno, src in lst})
    rows = []
    for cam_id, frame_no, src in jobs:
        if cam_id not in offsets:
            continue
        # Keep the untouched PNG in original_frames, geotagged JPG goes in the camera folder
        orig = os.path.join(original_dir, os.path.basename(src))
        if os.path.isfile(src):
            shutil.move(src, orig)
        elif not os.path.isfile(orig):
            raise FileNotFoundError(src)
        dst = os.path.splitext(src)[0] + ".jpg"
        src = orig
        ts = frame_no / fps
        lat0 = float(np.interp(ts, t_sec, lat))
        lon0 = float(np.interp(ts, t_sec, lon))
        dep0 = float(np.interp(ts, t_sec, depth))
        if attitude is not None:
            roll, pitch, hdg = attitude_at(attitude, ts)
            source = "ROV attitude"
        else:
            roll, pitch, hdg = 0.0, 0.0, estimate_heading_deg(t_sec, lat, lon, ts)
            source = "level ROV, heading from track"
        if heading_deg is not None:
            hdg, source = heading_deg, source + " (heading given)"

        right, fwd, up = offsets[cam_id]
        # Body FRD offset -> NED with the ROV attitude at this frame
        d_north, d_east, d_down = rotation_ned_body(roll, pitch, hdg) @ np.array([fwd, right, -up])
        m_lat, m_lon = _metres_per_degree(lat0)
        la = lat0 + d_north / m_lat
        lo = lon0 + d_east / m_lon
        de = dep0 + d_down  # depth positive down
        when = t0 + timedelta(seconds=ts)

        _save_jpg_with_gps(src, dst, la, lo, de, when)

        rows.append((os.path.basename(dst), cam_id, frame_no, when.strftime("%H:%M:%S.%f")[:-3],
                     la, lo, de, roll, pitch, hdg, d_east, d_north, d_down, source))
        print(f"[GEO-FL] {cam_id} frame={frame_no} roll={roll:.1f} pitch={pitch:.1f} heading={hdg:.1f} deg ({source}) "
              f"offset E={d_east:+.3f} N={d_north:+.3f} down={d_down:+.3f} m -> "
              f"lat={la:.8f} lon={lo:.8f} depth={de:.3f}")

    positions_csv = os.path.join(processed_dir, "first_last_geo_positions.csv")
    with open(positions_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Image", "Camera", "Frame", "Timestamp", "Latitude", "Longitude", "Depth",
                    "Roll_deg", "Pitch_deg", "Heading_deg", "OffsetEast_m", "OffsetNorth_m", "OffsetDown_m",
                    "AttitudeSource"])
        for r in rows:
            w.writerow([r[0], r[1], r[2], r[3], f"{r[4]:.8f}", f"{r[5]:.8f}", f"{r[6]:.3f}",
                        f"{r[7]:.1f}", f"{r[8]:.1f}", f"{r[9]:.1f}", f"{r[10]:.3f}", f"{r[11]:.3f}",
                        f"{r[12]:.3f}", r[13]])

    print(f"[GEO-FL] Geotagged {len(rows)} first/last frame(s) in the camera folders; "
          f"original PNGs kept in {original_dir}")
    return len(rows)


# =====================================================================
# External georeferencing: Cesium tileset.json (model in local coords)
# The bounding box is computed from the navigation track (replaces the older
# geolocate_glb.py, which used a fixed 40 m cube).
# =====================================================================
_WGS84_A = 6378137.0
_WGS84_F = 1.0 / 298.257223563
_WGS84_E2 = _WGS84_F * (2.0 - _WGS84_F)


def geodetic_to_ecef(lat_deg, lon_deg, h):
    """WGS84 (lat, lon, ellipsoidal height) -> ECEF (closed form, same as pyproj EPSG:4979->4978)."""
    lat = np.radians(np.asarray(lat_deg, dtype=float))
    lon = np.radians(np.asarray(lon_deg, dtype=float))
    h = np.asarray(h, dtype=float)
    n = _WGS84_A / np.sqrt(1.0 - _WGS84_E2 * np.sin(lat) ** 2)
    x = (n + h) * np.cos(lat) * np.cos(lon)
    y = (n + h) * np.cos(lat) * np.sin(lon)
    z = (n * (1.0 - _WGS84_E2) + h) * np.sin(lat)
    return np.stack([x, y, z], axis=-1)


def enu_rotation(lat_deg: float, lon_deg: float) -> np.ndarray:
    """3x3 matrix whose columns are the East, North, Up unit vectors expressed in ECEF."""
    lat, lon = np.radians(lat_deg), np.radians(lon_deg)
    east = np.array([-np.sin(lon), np.cos(lon), 0.0])
    north = np.array([-np.sin(lat) * np.cos(lon), -np.sin(lat) * np.sin(lon), np.cos(lat)])
    up = np.array([np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)])
    return np.column_stack([east, north, up])


def generate_external_georeferencing(
    nav_path: str,
    out_dir: str,
    *,
    model_uri: str = "model.glb",
    margin_m: float = 10.0,
    min_half_size_m: float = 5.0,
    orientation: str = "enu",
    switch_distance_m: float = 2000.0,
    flyto_range_m: float = 120.0,
) -> str:
    """
    Write <out_dir>/tileset.json (+ focus_config.json and origin.csv) for Cesium.

    - Origin = first navigation sample (start of the video), height = -Depth.
    - orientation="enu": transform = ENU frame at the origin (model local axes =
      East/North/Up, so a model reconstructed in local metric coords lies flat).
      orientation="translation": pure ECEF translation (no rotation).
    - boundingVolume.box: oriented box enclosing the whole navigation track,
      expressed in the tile's local frame, enlarged by margin_m on every side.
    """
    import json

    csv_path = resolve_nav_csv(nav_path)
    t0, t_sec, lat, lon, depth = load_navigation(csv_path)
    height = -depth  # depth is positive down

    lat0, lon0, h0 = float(lat[0]), float(lon[0]), float(height[0])
    origin_ecef = geodetic_to_ecef(lat0, lon0, h0)
    track_ecef = geodetic_to_ecef(lat, lon, height)

    if orientation == "enu":
        rot = enu_rotation(lat0, lon0)
    elif orientation == "translation":
        rot = np.eye(3)
    else:
        raise ValueError("orientation must be 'enu' or 'translation'")

    # Track expressed in the tile's local frame (row-wise rot.T @ (p - o))
    local = (track_ecef - origin_ecef) @ rot
    mins, maxs = local.min(axis=0), local.max(axis=0)
    center = (mins + maxs) / 2.0
    half = np.maximum((maxs - mins) / 2.0 + margin_m, min_half_size_m)

    transform = np.eye(4)
    transform[:3, :3] = rot
    transform[:3, 3] = origin_ecef

    box = [
        float(center[0]), float(center[1]), float(center[2]),
        float(half[0]), 0.0, 0.0,
        0.0, float(half[1]), 0.0,
        0.0, 0.0, float(half[2]),
    ]

    tileset = {
        "asset": {"version": "1.1"},
        "extensionsUsed": ["3DTILES_content_gltf"],
        "extensionsRequired": ["3DTILES_content_gltf"],
        "geometricError": 500,
        "root": {
            "transform": transform.T.flatten().tolist(),  # column-major
            "boundingVolume": {"box": box},
            "geometricError": 0,
            "refine": "ADD",
            "content": {"uri": model_uri},
        },
    }

    os.makedirs(out_dir, exist_ok=True)
    tileset_path = os.path.join(out_dir, "tileset.json")
    with open(tileset_path, "w") as f:
        json.dump(tileset, f, indent=2)

    focus = {
        "name": os.path.splitext(model_uri)[0],
        "position": {"latitude": lat0, "longitude": lon0, "height": h0},
        "model": {"uri": model_uri, "distanceDisplayCondition": {"near": 0.0, "far": float(switch_distance_m)}},
        "marker": {
            "type": "point", "pixelSize": 12, "color": "#00E5FF", "outlineColor": "#003344", "outlineWidth": 2,
            "distanceDisplayCondition": {"near": float(switch_distance_m), "far": 20000000.0},
            "onClick": "flyToModel",
        },
        "flyTo": {
            "destination": {"latitude": lat0, "longitude": lon0, "height": max(h0 + float(flyto_range_m), 5.0)},
            "rangeMeters": float(flyto_range_m),
        },
    }
    with open(os.path.join(out_dir, "focus_config.json"), "w") as f:
        json.dump(focus, f, indent=2)

    # Origin row (first navigation sample), for reference / other tools
    with open(os.path.join(out_dir, "origin.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Date", "Timestamp", "Latitude", "Longitude", "Depth"])
        w.writerow([t0.strftime("%Y-%m-%d"), t0.strftime("%H:%M:%S.%f")[:-3],
                    f"{lat0:.8f}", f"{lon0:.8f}", f"{depth[0]:.3f}"])

    ext = maxs - mins
    print(f"[EXT] Origin lat={lat0:.8f} lon={lon0:.8f} height={h0:.3f} (orientation={orientation})")
    print(f"[EXT] Track extent (local) x={ext[0]:.2f} m, y={ext[1]:.2f} m, z={ext[2]:.2f} m; "
          f"box half-axes = {half[0]:.2f}, {half[1]:.2f}, {half[2]:.2f} m (margin {margin_m:g} m)")
    print(f"[EXT] Wrote {tileset_path} (+ focus_config.json, origin.csv). Content uri: '{model_uri}'")
    return tileset_path
