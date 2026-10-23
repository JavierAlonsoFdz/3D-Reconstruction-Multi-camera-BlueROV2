# Tools

This folder contains general-purpose helper tools, such as a script for converting and trimming video files (for example, cutting a section out of a long recording).

## Video cropper (`converter.py`)

The original videos are usually recorded as `.mkv` files. Before extracting frames for reconstruction or calibration, the videos can be converted to `.mp4` and cropped to the timestamp range that contains the useful sequence.

> To crop the four camera videos of a mission **and** get the georeferenced position of the crop, use the [GUI navigation transects](../GUI%20navigation%20transects/) instead.

### Usage

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

### Requirement

This script requires `ffmpeg` to be installed and available in the system `PATH`.

