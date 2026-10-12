# Calibration GUI for Kalibr

This folder contains a graphical preprocessing tool for extracting synchronized calibration frames from the four BlueROV2 camera videos.

Run the calibration GUI:

![Calibration GUI](../../images/calibration%20GUI.png)

## Output Format

The output is automatically produced in a Kalibr-compatible format. The GUI creates a `processed_frames/` folder with one folder per camera:

```text
processed_frames/
+-- cam0_aux_right/
+-- cam1_aux_left/
+-- cam2_bottom_most/
+-- cam3_stereo_bottom/
```

Inside each camera folder, the frames follow Kalibr's required timestamp-like naming pattern:

```text
0000000100000000.jpg
0000000200000000.jpg
0000000300000000.jpg
...
```

All cameras share the same frame index so that the extracted image sets remain synchronized across the multi-camera system.

