#!/usr/bin/env python3
from pathlib import Path
import subprocess
import shutil
import argparse
from typing import List

def check_ffmpeg() -> None:
    """
    Raise RuntimeError if ffmpeg is not found in PATH.
    """
    if shutil.which("ffmpeg") is None:
        raise RuntimeError(
            "ffmpeg is not in PATH. Install it and add the executable folder to PATH.\n"
            "Example: download from https://ffmpeg.org or use choco: choco install ffmpeg"
        )

def convert_and_trim(
    input_path: Path,
    out_dir: Path,
    start: str,
    end: str,
    vcodec: str = "libx264",
    acodec: str = "aac",
    preset: str = "fast",
    crf: int = 23,
    audio_bitrate: str = "128k",
) -> None:
    """
    Convert and trim a single video (MKV or MP4) to MP4 using ffmpeg.
    """
    out_name = input_path.stem + ".mp4"
    out_path = out_dir / out_name

    cmd: List[str] = [
        "ffmpeg",
        "-y",
        "-i", str(input_path),
        "-ss", start,
        "-to", end,
        "-c:v", vcodec,
        "-preset", preset,
        "-crf", str(crf),
        "-c:a", acodec,
        "-b:a", audio_bitrate,
        str(out_path),
    ]

    print("Running:", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"ERROR processing {input_path.name}:\n{result.stderr}")
    else:
        print(f"Done: {out_path}")

def find_videos(folder: Path) -> List[Path]:
    """
    Return a list of .mkv and .mp4 files in the given folder.
    """
    videos = list(folder.glob("*.mkv"))
    videos += list(folder.glob("*.mp4"))
    return videos

def main() -> None:
    parser = argparse.ArgumentParser(description="Batch trim and convert MKV/MP4 -> MP4")
    parser.add_argument("--folder", "-f", required=True, help="Folder containing .mkv or .mp4 files")
    parser.add_argument("--out", "-o", help="Output folder (default: folder/converted)")
    parser.add_argument("--start", default="00:00:00", help="Start time (HH:MM:SS)")
    parser.add_argument("--end", default="00:00:10", help="End time (HH:MM:SS)")
    args = parser.parse_args()

    folder: Path = Path(args.folder).expanduser().resolve()
    if not folder.exists():
        raise SystemExit("The specified folder does not exist: " + str(folder))

    out_dir: Path = Path(args.out).expanduser().resolve() if args.out else folder / "converted"
    out_dir.mkdir(parents=True, exist_ok=True)

    check_ffmpeg()

    videos = find_videos(folder)
    if not videos:
        print("No .mkv or .mp4 files found in", folder)
        return

    for vid in videos:
        try:
            convert_and_trim(vid, out_dir, args.start, args.end)
        except Exception as e:
            print("Failed with", vid.name, "->", e)

if __name__ == "__main__":
    main()
