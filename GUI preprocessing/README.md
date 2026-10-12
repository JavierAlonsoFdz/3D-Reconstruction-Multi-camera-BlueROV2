# GUI Preprocessing

This folder contains the graphical preprocessing tool used to extract synchronized frames from the BlueROV2 multi-camera videos before 3D reconstruction.

Run the preprocessing GUI:

![Preprocessing GUI](../images/preprocessing%20GUI.png)

Select the path to the folder containing the BlueROV2 videos.

Then select:

- The keyframe extraction method:
  - `auto`: automatically selects representative frames using AAFE, a visual-odometry-based keyframe selection strategy.
  - `fixed`: extracts frames at a fixed interval.
- The filter applied to the frames:
  - `raw`: saves frames without enhancement.
  - `clear`: applies enhancement for clear-water conditions.
  - `turbid`: applies color restoration and filtering for turbid-water conditions.

Click **Start** to generate a `processed_frames/` folder containing the extracted frames for each camera in the structure expected by 3DF Zephyr.
