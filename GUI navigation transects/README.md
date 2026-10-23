# GUI Navigation Transects (Georeferencing GUI)

This folder contains a graphical interface to **crop the four multi-camera videos of a mission to a transect and georeference that transect** using the ROV navigation data.

Given a time range inside the videos (`Start` / `End`), the tool:

1. Cuts that range out of each of the four camera `.mkv` videos and converts it to `.mp4` (same `ffmpeg` settings as [`Tools/converter.py`](../Tools/)). Each `.mp4` keeps the real start time of the crop in its `creation_time` metadata.
2. Converts the video times to real (topside clock) times and interpolates the navigation data to obtain the position (latitude, longitude, depth) at the **first** and **last** frame of the crop.
3. Writes the four cropped videos and a CSV with those two positions into a new output folder.

The cropped videos can then be used directly in the [GUI preprocessing](../GUI%20preprocessing/) for keyframe extraction, and the CSV gives the georeferenced start and end of the transect.

## Files

- `gui_georeferencing.py`: Tkinter GUI.
- `georeferencing.py`: processing logic. It can also be used from the command line without the GUI.

## Inputs

The `videos/` and `output/` folders are **not included in the repository** (they are too large). To run the tool you need:

### 1. Videos folder

The four camera videos of the mission, as downloaded with the ROV GUI (**Save Videos**). The file names must contain the camera name:

| Camera | Name in file | Example |
|--------|--------------|---------|
| `cam0` | `aux_right` | `video_aux_right.mkv` |
| `cam1` | `aux_left` | `video_aux_left.mkv` |
| `cam2` | `bottom_most` | `video_bottom_most.mkv` |
| `cam3` | `stereo_bottom` | `video_stereo_bottom.mkv` |

`.mkv` and `.mp4` are accepted. The `cam0` (`aux_right`) video is required, as it is the time reference; the other cameras are processed if present.

When the ROV GUI downloads the videos, it writes the real recording start (corrected to the topside clock) into each `.mkv`. If that date is missing or not corrected, the tool falls back to a `recording_time.txt` file placed in the videos folder or its parent folder.

### 2. Navigation data folder

The navigation data folder of the **same mission**, exported by the ROV GUI. An example is included in [`navigation data/`](navigation%20data/):

- `*_cam0_transformed.csv`: navigation transformed to the position of `cam0` (camera offset and mount already applied). Used with the **cam0** position option (default).
- `*_navigation.csv`: navigation of the vehicle itself (IMU). Used with the **Vehicle** position option.
- `recording_time.txt`: recording start in the topside clock (fallback time reference).
- `*_export.log`, `*_track.png`: export log and track plot (informative only).

The CSV files must contain at least the columns `timestamp`, `lat_deg`, `lon_deg` and `depth_m`. The folder must contain exactly one CSV of the selected type (it is searched recursively), or the CSV itself can be selected.

### 3. Crop times

`Start` and `End` as times **inside the video** in `HH:MM:SS` format (decimals allowed, e.g. `00:03:00.5`). The selected range must lie inside both the video length and the navigation time span.

## Usage

Run the GUI:

```bash
python gui_georeferencing.py
```

In the GUI:

1. **Mission**: select the `Videos`, `Navigation data` and `Output` folders. By default they point to `videos/`, `navigation data/` and `output/` inside this folder. Once selected, the GUI shows the start date, length and fps of the videos and the time span of the navigation data expressed in video time, which helps to choose the crop.
2. **Crop**: enter `Start` and `End` (time inside the video).
3. **Position**: choose `cam0 (aux_right)` (position of the camera) or `Vehicle` (position of the ROV).
4. Press **Start**. Progress is shown in the log. **Cancel** stops the current `ffmpeg` conversion.

The same process can be run from the command line:

```bash
python georeferencing.py --videos videos --navigation "navigation data" --start 00:00:05 --end 00:00:25 [--out output] [--position cam0|vehicle]
```

## Output

A folder `<mission>_<HHMMSS start>_<HHMMSS end>` is created inside the output folder, where the times are the real (topside clock) start and end of the crop:

```
output/
└── multicamera_recording_161229_161235_161255/
    ├── multicamera_recording_161229_161235_161255.csv
    ├── video_aux_left.mp4
    ├── video_aux_right.mp4
    ├── video_bottom_most.mp4
    └── video_stereo_bottom.mp4
```

The CSV contains the position at the first and last frame of the crop:

```csv
Date,Timestamp,Latitude,Longitude,Depth
2026-10-20,16:12:35.264,55.78323936,12.51413545,0.939
2026-10-20,16:12:55.264,55.78321505,12.51409064,1.014
```

## Requirements

- Python standard library only (Tkinter for the GUI).
- `ffmpeg` and `ffprobe` installed and available in the system `PATH`.
