# GUI Preprocessing

Extracts synchronized frames from the 4 BlueROV2 camera videos for 3D reconstruction and, if navigation data is given, georeferences them for 3DF Zephyr (EXIF) and Cesium (`tileset.json`).

![Preprocessing GUI](../images/preprocessing%20GUI.png)

## Run

```bash
python gui_preprocessing.py
```

1. **Dataset**: folder with the 4 videos (filenames must contain `aux_right`, `aux_left`, `bottom_most`, `stereo_bottom`). Typically a transect cropped with the [GUI Navigation Transects](../GUI%20navigation%20transects/).
2. **Navigation data** (optional): the transect CSV, ideally `*_all_timestamps.csv` from the GUI Navigation Transects. Leave empty to skip georeferencing.
3. **Keyframes**: `Auto` (AAFE, optical-flow / feature-matching based selection) or `Fixed` (one frame every N).
4. **Filtering**: `Raw`, `Turbid` (white balance + CLAHE) or `Clear` (subtle sharpening).
5. **Start**.

Command line: `python main_preprocessing.py <dataset> [--keyframe-mode auto|fixed] [--mode raw|clear|turbid] [--nav <csv>]`.

## Output

```text
<dataset>/processed_frames/
+-- cam0_aux_right/ ... cam3_stereo_bottom/   camX_img_<frame>_best.png (same frame = same instant)
+-- keyframes.txt                              selected frames (Auto mode)
# only with navigation data:
+-- camX_img_<first|last>_best.jpg            (in each camera folder) geotagged first/last frames
+-- original_frames/                           untouched PNGs of the first/last frames
+-- first_last_geo_positions.csv               position used for each geotagged frame
+-- external_georeferencing/
    +-- tileset.json, focus_config.json, origin.csv
```

## Georeferencing (with navigation data)

The video and the navigation CSV start together, so frame *n* is at `t0 + n / fps`; position is interpolated from the CSV.

- **Internal (3DF Zephyr, EXIF)**: the first and last frame of **each camera** are replaced by a JPG with GPS EXIF (latitude, longitude, depth below sea level, capture time). Each camera gets its own position: cam0 position plus the rig offsets (22 x 11 cm plate, pitched 40° down), rotated with the ROV roll/pitch/heading if the CSV has them (exported by the GUI Navigation Transects); otherwise the ROV is assumed level and the heading is taken from the track. Import the camera folders into Zephyr as usual and it reads the EXIF GPS to georeference the project. The georeferenced model can be exported from Zephyr directly in a Cesium ion-compatible format.

  ![3DF Zephyr Cesium export](../images/export.png)

- **External (Cesium, `tileset.json`)**: for a model built in local coordinates (use the PNGs in `original_frames/` instead of the geotagged JPGs). `tileset.json` places the model at the first navigation position with an East-North-Up orientation and a bounding box fitted to the navigation track. Export the model as `<dataset>.glb` and upload it together with `tileset.json` to Cesium ion as a 3D Tiles asset.

## Files

| File | Purpose |
|------|---------|
| `gui_preprocessing.py` | GUI (Tkinter) |
| `main_preprocessing.py` | Frame extraction pipeline (also CLI) |
| `key_frame_selection_auto.py` | AAFE automatic keyframe selection |
| `filter.py` | Turbid / clear water filters |
| `nav_georef.py` | Navigation interpolation, EXIF geotagging and `tileset.json` |
