import os
import csv
import sys
import subprocess
import shutil
from typing import List, Tuple
from PIL import Image

def _resolve_exiftool_path() -> str:
    """Return the absolute path to exiftool.exe located in the repo's tools folder.

    Falls back to "exiftool.exe" in PATH if the tools copy is not found.
    """
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        # Project root is three levels up from this script:
        # Research Assistant / 3D reconstruction / georef / Internal ... / geotag_images_exif.py
        project_root = os.path.abspath(os.path.join(script_dir, "..", "..", ".."))
        # Common exiftool executable names on Windows
        candidates = [
            # Directly under tools
            os.path.join(project_root, "tools", "exiftool.exe"),
            os.path.join(project_root, "tools", "exiftool(-k).exe"),
            # Inside tools\exiftool directory (as provided by user)
            os.path.join(project_root, "tools", "exiftool", "exiftool.exe"),
            os.path.join(project_root, "tools", "exiftool", "exiftool(-k).exe"),
        ]
        for c in candidates:
            if os.path.isfile(c):
                return c
    except Exception:
        pass
    # Fallback: rely on system PATH
    return "exiftool.exe"

EXIFTOOL = _resolve_exiftool_path()

def _normalize_headers(headers: List[str]) -> List[str]:
    return [h.strip().lower() for h in headers]


def load_first_last_positions(csv_path: str) -> Tuple[Tuple[float, float, float], Tuple[float, float, float], int]:
    """Read the CSV and return (lat, lon, depth) for the first and last valid rows, plus valid row count.

    - Handles headers case-insensitively and ignores surrounding spaces.
    - If a Timestamp_Num column exists, it is not needed for this workflow.
    Returns: (first_pos, last_pos, valid_count)
    """
    first_pos = None
    last_pos = None
    valid_count = 0

    with open(csv_path, newline="") as csvfile:
        reader = csv.DictReader(csvfile)
        if reader.fieldnames is None:
            raise ValueError("CSV has no headers.")
        norm_headers = _normalize_headers(reader.fieldnames)

        # Flexibly map column names
        def find_col(*candidates: str) -> int:
            for c in candidates:
                if c in norm_headers:
                    return norm_headers.index(c)
            return -1

        lat_idx = find_col("latitude", "lat")
        lon_idx = find_col("longitude", "lon", "long")
        depth_idx = find_col("depth", "altitude", "z")

        if lat_idx == -1 or lon_idx == -1 or depth_idx == -1:
            raise ValueError("CSV must contain Latitude, Longitude, and Depth columns (similar names accepted).")

        for row in reader:
            row_list = [row[h] for h in reader.fieldnames]
            try:
                lat = float(row_list[lat_idx])
                lon = float(row_list[lon_idx])
                depth = float(row_list[depth_idx])
                pos = (lat, lon, depth)
            except (ValueError, TypeError):
                # Skip invalid rows
                continue

            if first_pos is None:
                first_pos = pos
            last_pos = pos  # keep updating until the last valid one
            valid_count += 1

    if first_pos is None:
        raise ValueError("No valid rows found in CSV.")

    # If there is only one valid row, first and last are the same
    if last_pos is None:
        last_pos = first_pos

    return first_pos, last_pos, valid_count



def convert_png_to_jpg(src_path: str, dst_path: str) -> None:
    with Image.open(src_path) as img:
        rgb_img = img.convert("RGB")
        rgb_img.save(dst_path, "JPEG", quality=95)


def write_gps_to_image(image_path: str, lat: float, lon: float, depth: float) -> None:
    subprocess.run([
        EXIFTOOL,
        "-n",
        f"-GPSLatitude={lat}",
        f"-GPSLatitudeRef={'N' if lat >= 0 else 'S'}",
        f"-GPSLongitude={lon}",
        f"-GPSLongitudeRef={'E' if lon >= 0 else 'W'}",
        f"-GPSAltitude={abs(depth)}",
        "-GPSAltitudeRef=1",  # 1 = Below sea level
        "-overwrite_original",
        image_path
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def process_first_last_images(
    image_dir: str,
    csv_path: str,
    output_dir: str = "geotagged_jpg"
) -> None:
    os.makedirs(output_dir, exist_ok=True)

    try:
        first_pos, last_pos, valid_count = load_first_last_positions(csv_path)
    except Exception as e:
        print(f"❌ Error reading CSV: {e}")
        return

    images = [
        f for f in os.listdir(image_dir)
        if f.lower().endswith(".png") and os.path.isfile(os.path.join(image_dir, f))
    ]
    if not images:
        print("❌ No PNG images found in the specified folder.")
        return

    # Select the first and last image by file modification timestamps
    # (first = oldest; last = most recent)
    full_paths = [os.path.join(image_dir, f) for f in images]
    first_img = os.path.basename(min(full_paths, key=os.path.getmtime))
    last_img = os.path.basename(max(full_paths, key=os.path.getmtime))

    to_process = []

    # If the CSV has only one valid entry: use it as the start time and only tag the first image.
    if valid_count == 1:
        to_process.append((first_img, first_pos))
    else:
        # Two or more entries: keep existing behavior (first and last images)
        to_process.append((first_img, first_pos))
        if last_img != first_img:
            to_process.append((last_img, last_pos))

    for img, (lat, lon, depth) in to_process:
        src_path = os.path.join(image_dir, img)
        jpg_name = os.path.splitext(img)[0] + ".jpg"
        dst_path = os.path.join(output_dir, jpg_name)

        convert_png_to_jpg(src_path, dst_path)
        write_gps_to_image(dst_path, lat, lon, depth)

    print(f"✅ Converted and geotagged {len(to_process)} image(s) into '{output_dir}'.")


# ---------------- MAIN -------------------
if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python3 geotag_images_exif.py <image_dir> <csv_path>")
        sys.exit(1)

    image_dir = sys.argv[1]
    csv_path = sys.argv[2]

    process_first_last_images(image_dir, csv_path)
