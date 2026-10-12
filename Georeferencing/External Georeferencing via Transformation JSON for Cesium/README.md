# External Georeferencing via Transformation JSON for Cesium

In this workflow, the reconstructed 3D model is externally georeferenced for visualization in Cesium using a rigid transformation defined in a `tileset.json` file. This transformation places the model at its correct geographic location on the globe using Earth-Centered, Earth-Fixed (ECEF) coordinates.

The script used for this step is:

```text
geolocate_glb.py
```

## Directory Structure

```text
External Georeferencing via Transformation JSON for Cesium/
+-- geolocate_glb.py
+-- input example/
|   +-- Cannon.glb
|   +-- 22_05_2025_ptsag_102940.001-103010.02.csv
|   +-- README.md
+-- output example/
    +-- transformation.json
```

## Script Usage

Run:

```bash
python3 geolocate_glb.py <model.glb> <input.csv> <output_tileset.json>
```

Optional focus configuration output:

```bash
python3 geolocate_glb.py <model.glb> <input.csv> <output_tileset.json> <output_focus_config.json>
```

Where:

- `<model.glb>` is the 3D model exported from 3DF Zephyr in GLB format.
- `<input.csv>` is the CSV file containing the geographic position associated with the start of the video sequence. Only a single representative position is required for this step.
- `<output_tileset.json>` is the output `tileset.json` file containing the transformation required to correctly position the model in Cesium.
- `<output_focus_config.json>` is optional and can be used to generate a Cesium focus/fly-to configuration.

Example:

```bash
python3 geolocate_glb.py \
  "input example/Cannon.glb" \
  "input example/22_05_2025_ptsag_102940.001-103010.02.csv" \
  "output example/tileset.json"
```

## Cesium Visualization

After generating the JSON file, upload the `.glb` model together with the generated `tileset.json` to Cesium ion as a 3D Tiles asset.

Cesium will use the ECEF transformation in the `tileset.json` file to place the model at the correct geographic location.
