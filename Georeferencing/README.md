# Georeferencing

The BlueROV2 is equipped with a DVL system. Once the starting coordinates of the ROV are provided, the system can record georeferenced navigation data such as latitude, longitude, and depth for each recorded video sequence.

This information is later used to georeference the reconstructed 3D models. After generating a suitable `.csv` file containing the first and last positions of the sequence, there are two possible georeferencing workflows depending on the target application:

1. [External Georeferencing via Transformation JSON for Cesium](External%20Georeferencing%20via%20Transformation%20JSON%20for%20Cesium/)
2. [Internal Georeferencing via EXIF metadata for 3DF Zephyr](Internal%20Georeferencing%20via%20EXIF%20metadata%20for%203DF%20Zephyr/)
