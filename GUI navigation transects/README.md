# GUI Navigation Transects

Crops the 4 camera videos of a mission to a transect, converts them to MP4 and writes the georeferenced navigation of that transect (position and ROV attitude). The output folder is the input of the [GUI Preprocessing](../GUI%20preprocessing/).

![GUI Navigation Transects](../images/navigation%20transects%20GUI.png)

## Run

```bash
python gui_georeferencing.py
```

1. **Mission**: select the `Videos`, `Navigation data` and `Output` folders. The GUI shows the video start, length and fps, and the navigation time span in video time.
2. **Crop**: `Start` and `End` as times inside the video (`HH:MM:SS`, decimals allowed).
3. **Position**: `cam0 (aux_right)` (default) or `Vehicle`.
4. **Start**. **Cancel** stops the current `ffmpeg` conversion.

Command line:

```bash
python georeferencing.py --videos <dir> --navigation <dir> --start 00:00:05 --end 00:00:25 [--out <dir>] [--position cam0|vehicle]
```

## Inputs (not included in the repository)

- **Videos**: the 4 `.mkv`/`.mp4` videos downloaded with the ROV GUI; names must contain `aux_right`, `aux_left`, `bottom_most`, `stereo_bottom`. `cam0` is required (time reference). The real recording start is read from the `.mkv` metadata, or from `recording_time.txt` as fallback.
- **Navigation data**: the mission folder exported by the ROV GUI:
  - `*_cam0_transformed.csv`: position of cam0 (used with `cam0`).
  - `*_navigation.csv`: position of the vehicle (used with `Vehicle`) and **ROV attitude** (roll, pitch, heading).
  - `recording_time.txt`: fallback time reference.

## Output

```text
<output>/<mission>_<HHMMSS start>_<HHMMSS end>/
+-- <name>.csv                   position at the first and last frame of the crop
+-- <name>_all_timestamps.csv    every navigation row of the crop (ends interpolated)
+-- video_aux_left.mp4, video_aux_right.mp4, video_bottom_most.mp4, video_stereo_bottom.mp4
```

Both CSVs have the columns `Date, Timestamp, Latitude, Longitude, Depth, Roll, Pitch, Heading`. Roll/Pitch/Heading are the ROV body attitude in degrees (pitch < 0 = nose down, heading clockwise from North); they are empty if `*_navigation.csv` is missing. The GUI Preprocessing uses them to place cam1–cam3 relative to cam0 when the ROV is tilted.

## Requirements

Python standard library (Tkinter) and `ffmpeg`/`ffprobe` in the system `PATH`.
