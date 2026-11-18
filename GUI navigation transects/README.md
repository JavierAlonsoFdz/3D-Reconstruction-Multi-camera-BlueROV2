# GUI Navigation Transects

Crops the 4 camera videos of a mission to a transect, converts them to MP4 and writes the georeferenced navigation of **every frame** of that transect (cam0 position, depth, altitude, ROV attitude, cam0 orientation). The output folder is the input of the [GUI Preprocessing](../GUI%20preprocessing/).

![GUI Navigation Transects](../images/navigation%20transects%20GUI.png)

## Run

```bash
python gui_georeferencing.py
```

1. **Mission**: select the `Videos`, `Navigation data` and `Output` folders. The GUI shows the video start, length and fps, and which part of the video has navigation.
2. **Crop**: `Start` and `End` as times inside the video (`HH:MM:SS`, decimals allowed). The crop holds the frames whose time is in [Start, End).
3. **Start**. **Cancel** stops the current `ffmpeg` conversion.

Command line:

```bash
python georeferencing.py --videos <dir> --navigation <dir> --start 00:00:03 --end 00:00:10 [--out <dir>]
```

## Inputs (not included in the repository)

- **Videos**: the 4 `.mkv`/`.mp4` videos downloaded with the ROV GUI (Save Videos); names must contain `aux_right`, `aux_left`, `bottom_most`, `stereo_bottom`. `cam0` (aux_right) is required. The real recording start is read from the `.mkv` metadata (corrected to the topside clock by Save Videos), or from `recording_time.txt` as fallback.
- **Navigation data**: the mission folder exported by the ROV GUI. Any of:
  - `*_cam0_frames.csv` (ROV GUI, Save Videos): the cam0 pose of every video frame. Used when present.
  - `*_cam0_transformed.csv`: position of cam0. For missions saved before `*_cam0_frames.csv` existed, the same per-frame table is built from it and the frame times of the cam0 video (identical result).
  - `*_navigation.csv`: ROV attitude (roll, pitch, heading). Optional: without it, the ROV attitude is recovered from the cam0 attitude and the camera mount pitch (−40°).

## Output

```text
<output>/<mission>_<HHMMSS first frame>_<HHMMSS last frame>/
+-- <name>_frames.csv            ONE ROW PER FRAME of the crop, all the information (see below)
+-- video_aux_left.mp4, video_aux_right.mp4, video_bottom_most.mp4, video_stereo_bottom.mp4
```

**Frame numbering.** The crop is cut by frames, not by time: exactly the same frames from the 4 cameras (they are frame-synchronised: same start, same frame count). `Frame` starts at **0** with the first frame of the MP4s, so frame *k* of every MP4 is the row with `Frame = k` (the GUI Preprocessing names it `cam0_img_<k>_best.png`). Checked image by image: MP4 frame 0 is source frame 90 for a crop starting at 00:00:03.

**Timing.** Frame times are the video's recording date (corrected to the topside clock, the same clock as the navigation) plus each frame's presentation time; checked against the yaw rotation seen in the video to within ±0.05 s (under 2 frames). The offset between the start of the video and the start of the navigation (0.6–1.4 s) is therefore already accounted for.

`<name>_frames.csv` columns:

| Column | |
|---|---|
| `Frame` | Frame number in the MP4s, from 0 |
| `SourceFrame` | Frame number in the original (uncropped) video |
| `VideoTime_s` | Seconds from the first frame of the crop |
| `Date`, `Timestamp` | Capture time of the frame (topside clock, ms) |
| `Latitude`, `Longitude`, `Depth` | Position of cam0 (aux_right); depth positive down |
| `Roll`, `Pitch`, `Heading` | ROV body attitude, degrees (pitch < 0 = nose down, heading clockwise from North) |
| `Altitude` | Height above the seafloor (DVL; filtered when the mission has `altitude_filtered_m`) |
| `CamRoll`, `CamPitch`, `CamHeading` | cam0 viewing direction (pitch −40° = 40° below the horizon with the ROV level) |
| `qx, qy, qz, qw` | cam0 orientation: optical axes (x right, y down, z view) → NED |
| `North`, `East`, `Down` | Local position (m) of the ROV EKF frame |

Empty cells: frames before or after the navigation recording (the log says how many). The GUI Preprocessing reads this file (it looks for `*_frames.csv` in the transect folder) and geotags frame *n* with the row `Frame = n`.

## Requirements

Python standard library (Tkinter) and `ffmpeg`/`ffprobe` in the system `PATH`.
