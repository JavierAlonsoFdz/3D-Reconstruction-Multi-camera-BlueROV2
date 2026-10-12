# 3D Reconstruction Multi-Camera BlueROV2

This repository contains tools for 3D reconstruction using a multi-camera setup mounted on a BlueROV2 platform. 

The project is developed at DTU Aqua and focuses on underwater image preparation and preprocessing, camera calibration and georeferencing of generated 3D models.


The workflow is designed for datasets captured with four synchronized cameras and supports both clear-water and turbid-water conditions.

<img src="images/ROV_4cam.jpg" alt="BlueROV2 with four-camera system" width="565">
<img src="images/multi-camera%20rig.png" alt="Multi-camera rig" width="578">

## Project Overview

The main workflow includes:

1. **[Calibration](Calibration/)**
   - Kalibr-compatible calibration inputs and outputs.
   - A dedicated [Calibration GUI for Kalibr](Calibration/Calibration%20GUI%20for%20Kalibr/) for generating Kalibr-compatible image sets.
   - Conversion tools for exporting calibration parameters to formats usable in [3DF Zephyr](Calibration/3DF%20Zephyr/).

2. **[Video cropper](Video%20cropper/)**
   - Tools for cropping and converting video files to a common format.
   
3. **[GUI Preprocessing](GUI%20preprocessing/)**
   - A graphical interface for selecting preprocessing options.
   - Support for three image-processing modes:
     - `raw`: extracts frames without image enhancement.
     - `clear`: applies enhancement intended for clear-water imagery.
     - `turbid`: applies color restoration and filtering intended for turbid-water imagery.
   - Support for two keyframe selection strategies:
     - `fixed`: extracts frames at a fixed interval.
     - `auto`: selects representative frames using AAFE, a visual-odometry-based keyframe selection strategy.

4. **[Georeferencing](Georeferencing/)**
   - [Internal georeferencing](Georeferencing/Internal%20Georeferencing%20via%20EXIF%20metadata%20for%203DF%20Zephyr/) by writing GPS/depth metadata into image EXIF fields for use in 3DF Zephyr.
   - [External georeferencing](Georeferencing/External%20Georeferencing%20via%20Transformation%20JSON%20for%20Cesium/) by generating transformation data for Cesium-based visualization of exported 3D models.


## Typical Workflow

1. Capture synchronized video from the BlueROV2 multi-camera system.
2. Calibrate the cameras using Kalibr or an equivalent calibration workflow.
3. Convert calibration outputs if needed for 3DF Zephyr.
4. Crop the relevant section of the video and convert it to a suitable format, such as MP4.
5. Extract keyframes using the preprocessing GUI.

   **Image enhancement mode:**

   - `raw`: extracts frames without image enhancement.
   - `clear`: applies image enhancement for clear-water conditions.
   - `turbid`: applies color restoration and filtering for turbid-water conditions.

   **Keyframe extraction strategy:**

   - `fixed`: extracts frames at regular frame intervals.
   - `auto`: selects keyframes automatically using optical-flow-based motion analysis.

6. Reconstruct the 3D model in external photogrammetry software.
7. Georeference the model internally with EXIF metadata or externally with Cesium transformation files.

## Dependencies

The Python dependencies are listed in `requirements.txt`. Install them with:

```bash
pip install -r requirements.txt
```

The requirements file includes the main Python packages used by the project:

- OpenCV
- NumPy
- Pillow
- pandas
- pyproj
- PyYAML

Some workflows also require external command-line tools that are not installed with `pip`:

- `ffmpeg`: required by the video cropper/converter workflow.
- `exiftool`: required by the EXIF georeferencing workflow.
