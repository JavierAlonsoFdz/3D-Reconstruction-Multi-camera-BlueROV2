# Calibration GUI for Kalibr

Graphical tool that takes the **4 full camera videos** of a BlueROV2 calibration dive and automatically extracts, in Kalibr format, only the **synchronized frames where the AprilGrid is fully visible in all 4 cameras at the same time**.

No manual trimming is needed: record the whole calibration dive, point the GUI at the folder with the 4 videos and press **Start**.

![Calibration GUI](../../images/calibration%20GUI.png)

## Files

| File | Purpose |
|------|---------|
| `calibration_gui_automatic.py` | The GUI (Tkinter). |
| `main_automatic.py` | Processing pipeline; can also be used from the command line. |
| `filter.py` | Optional image enhancement filters (turbid / clear water). |

Requirements: Python 3 with `opencv-contrib-python` / `opencv-python` (>= 4.7, needs `cv2.aruco`) and `numpy`.

## Run

```bash
python calibration_gui_automatic.py
```

1. **Dataset**: select the folder containing the 4 videos. Filenames must contain the camera token
   (`aux_right`, `aux_left`, `bottom_most`, `stereo_bottom`), e.g. `video_aux_right.mkv`.
   The GUI shows in green/red which camera videos were found.
2. **Frame selection**
   - **Interval (N)** (default `10`): minimum spacing, in frames, between saved frames. Inside continuous runs where the 4 cameras see the board, one frame every N frames is kept; isolated good moments are kept too.
   - **Require the FULL 6x6 board** (default on): a moment is only kept if the whole board is inside the image of all 4 cameras (no cut boards).
   - **Reuse previous detection scan** (default on): if the folder was already processed, the detections stored in `detections.csv` are reused, so changing the interval/advanced values regenerates the frames in ~2 min instead of rescanning (~8 min for 4 x 4 min of 1080p video).
3. **Advanced** (collapsed, defaults are fine in most cases)
   - **Min tags per camera** (default `8`): minimum AprilGrid tags every camera must detect. Ensures enough tags to reliably estimate where the whole board lies.
   - **Border margin (px)** (default `10`): safety gap between the full board contour and the image edge. Only used with *FULL board* (greyed out otherwise).
   - **Scan step** (default `5`): the board is checked every K frames; only checked frames can be saved. `1` finds every possible frame but is ~5x slower. Should divide the interval.
4. **Filtering**: `Raw` (recommended for Kalibr), `Turbid` or `Clear`. Applied only to the saved images; detection always uses the raw frames.
5. **Start**. Progress and a summary of the detected runs are shown in the log. **Cancel** stops the process cleanly. If `processed_frames/` already contains images, the GUI asks before replacing them.

## How frames are selected

The target is the Kalibr AprilGrid described in `../Kalibr/april_6x6_80x80cm.yaml` (6x6 tag36h11, tag size 0.088 m, spacing 0.3).

1. **Scan**: every *scan step* frames, the AprilGrid tags are detected in each of the 4 videos (in parallel). Kalibr tags have a 2-bit black border, which the detector is configured for.
2. **Full-board check**: underwater turbidity means not all 36 tags are detected even when the board is fully visible, so "full board" is decided geometrically: with the detected tags (spread over at least 4 rows and 4 columns), a homography board -> image is fitted and the **outer contour of the whole board** (including the black corner squares) is projected. The board is considered fully visible if that contour is at least *border margin* px inside the image.
3. **Covisibility**: a frame is valid only if **all 4 cameras** pass the checks (min tags + full board).
4. **Sampling**: a valid frame is kept if it is at least *interval* frames after the last kept one.
5. **Write**: the selected frames are saved from the 4 videos with a shared index.

## Output format

Created inside the dataset folder, directly usable by Kalibr (`kalibr_bagcreater`, see `../Kalibr/README.md`):

```text
processed_frames/
+-- cam0_aux_right/
+-- cam1_aux_left/
+-- cam2_bottom_most/
+-- cam3_stereo_bottom/
+-- detections.csv
```

Inside each camera folder the frames follow Kalibr's timestamp-like naming pattern; the same file name corresponds to the same instant in the 4 cameras:

```text
0000000100000000.jpg
0000000200000000.jpg
0000000300000000.jpg
...
```

`detections.csv` contains, for every scanned frame: time, number of tags and board margin (px, negative = board cut by the image edge, `-9999` = board not found) for each camera, and whether the frame was covisible / selected and its output index. Useful to check why few frames were selected and which camera is limiting.

## Command line

The same pipeline without GUI:

```bash
python main_automatic.py <dataset_folder> [--interval 10] [--min-tags 8] [--margin-px 10]
                         [--scan-step 5] [--allow-partial] [--mode raw|turbid|clear]
                         [--reuse-scan] [--overwrite]
```

`--allow-partial` disables the full-board requirement (only `--min-tags` is used).

## Recording tips

Only moments where the whole board fits in all 4 cameras are used, so the way the calibration dive is recorded determines how many (and how useful) frame sets you get:

- **Distance**: keep the board at a distance where it fills roughly 1/3 to 1/2 of the image, so it fits in all 4 cameras at once. Close-ups where the board is cut in some camera are discarded.
- **Coverage**: sweep the board across the image (all 4 corners and edges, not only the centre). This is what constrains the distortion and the principal point.
- **Tilt**: view the board at ~30-45° to the left, right, up and down; without tilted views the focal length is poorly determined.
- **Depth**: make passes at 2-3 different distances.
- **Speed**: move slowly to avoid motion blur (turbidity already reduces sharpness).

A good 3-5 minute recording is better than a longer one made mostly of close-ups. Check `detections.csv` to see which camera limits the selection (most negative `board_margin_px`).

## Next steps

1. Copy `processed_frames/` next to the target YAML and run Kalibr as described in [`../Kalibr/README.md`](../Kalibr/README.md).
2. Convert the resulting `camchain-*.yaml` to 3DF Zephyr XML files with [`../3DF Zephyr/`](../3DF%20Zephyr/).
