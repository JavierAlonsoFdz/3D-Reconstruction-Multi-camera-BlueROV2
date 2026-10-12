# Kalibr Multi-Camera Calibration

This folder contains the files needed to run multi-camera calibration with Kalibr using the synchronized image folders extracted from the calibration GUI.

## Required Files

Before running Kalibr, prepare a calibration folder with:

- A folder containing the extracted synchronized frames, for example `frames/`.
- One subfolder per camera:
  - `cam0_aux_right/`
  - `cam1_aux_left/`
  - `cam2_bottom_most/`
  - `cam3_stereo_bottom/`
- The checkerboard target configuration file:
  - `april_6x6_80x80cm.yaml`
- Optionally, the local helper script:
  - `multicam_bag_creator`

An example image-folder layout is provided in `input example/`.

## Prerequisites

Kalibr is run through Docker. On Windows, install:

- WSL with Ubuntu
- Docker Desktop

Once the folder structure and required files are ready, run Kalibr using the Docker image from Stereolabs.

## Start the Kalibr Docker Container

From WSL/Ubuntu, run:

```bash
docker run -it --name Kalibr \
  -e DISPLAY=$DISPLAY \
  -e QT_X11_NO_MITSHM=1 \
  -v /tmp/.X11-unix:/tmp/.X11-unix:rw \
  -v "<local_path_to_calibration_folder>:/Calibration" \
  stereolabs/kalibr
```

Replace `<local_path_to_calibration_folder>` with the local path that contains the extracted frames, target YAML, and calibration scripts.

Inside the container, source the Kalibr workspace:

```bash
source kalibr_workspace/devel/setup.bash
```

## Create the Multi-Camera Bag File

Generate a multi-camera `.bag` file containing one topic per camera:

```bash
rosrun kalibr kalibr_bagcreater \
  --folder /Calibration/frames \
  --output-bag /Calibration/all_cams_calibration.bag
```

If your extracted frames folder has another name, replace `/Calibration/frames` with the correct mounted path, for example `/Calibration/processed_frames`.

Verify that all camera topics have the same number of messages:

```bash
rosbag info /Calibration/all_cams_calibration.bag
```

## Run Multi-Camera Calibration

Run the calibration process:

```bash
rosrun kalibr kalibr_calibrate_cameras \
  --models pinhole-radtan pinhole-radtan pinhole-radtan pinhole-radtan \
  --bag /Calibration/all_cams_calibration.bag \
  --topics /cam0_aux_right/image_raw \
           /cam1_aux_left/image_raw \
           /cam2_bottom_most/image_raw \
           /cam3_stereo_bottom/image_raw \
  --target /Calibration/april_6x6_80x80cm.yaml
```

The topic names must match the topics reported by `rosbag info`. If a different bag creation workflow is used, update the `--topics` argument accordingly.

## Output Files

After a successful calibration, Kalibr generates output files in the working directory. The names usually follow this pattern:

```text
camchain-Calibrationall_cams_calibration.yaml
results-cam-Calibrationall_cams_calibration.txt
report-cam-Calibrationall_cams_calibration.pdf
```

The output formats are:

- `.pdf`: diagnostic plots, including reprojection error distribution and detection quality.
- `.txt`: human-readable calibration summary.
- `.yaml`: complete intrinsic and extrinsic calibration parameters.

The YAML file is the main reference for downstream processing, including building camera models and importing accurate calibration into 3DF Zephyr or other photogrammetry and 3D reconstruction tools.

Example outputs are provided in `output example/`.

## Copy Results Back to the Mounted Folder

If the output files do not appear on your local machine, exit the container and copy them manually from the container into the mounted `/Calibration` folder:

```bash
docker exec Kalibr mv /camchain-Calibrationall_cams_calibration.yaml /Calibration/
docker exec Kalibr mv /results-cam-Calibrationall_cams_calibration.txt /Calibration/
docker exec Kalibr mv /report-cam-Calibrationall_cams_calibration.pdf /Calibration/
```

## Note About the Local Bag Creator

This folder also includes `multicam_bag_creator`, a helper script that converts synchronized multi-camera image folders into a ROS bag file with one topic per camera. If this script is used instead of `kalibr_bagcreater`, check the generated topics with:

```bash
rosbag info <generated_bag_file>
```

Then use those exact topic names in the `kalibr_calibrate_cameras --topics` argument.

