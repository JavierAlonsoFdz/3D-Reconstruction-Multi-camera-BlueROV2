# Video Cropper

This folder contains a helper script for converting and trimming the original full-length camera videos before preprocessing.

The original videos are usually recorded as `.mkv` files. Before extracting frames for reconstruction or calibration, the videos can be converted to `.mp4` and cropped to the timestamp range that contains the useful sequence.

## Usage

Run:

```bash
python3 converter.py \
  -f "Folder containing .mkv or .mp4 files" \
  -o "Output folder" \
  --start 00:03:00 \
  --end 00:22:51
```

If `-o` is not provided, the script creates a `converted/` folder inside the input folder.

Example:

```bash
python3 converter.py -f "./raw_videos" -o "./converted_videos" --start 00:03:00 --end 00:22:51
```

The script processes all `.mkv` and `.mp4` files found in the selected folder.

## Requirement

This script requires `ffmpeg` to be installed and available in the system `PATH`.

