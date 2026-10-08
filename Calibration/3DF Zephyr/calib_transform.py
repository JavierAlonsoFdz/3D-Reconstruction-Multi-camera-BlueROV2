#!/usr/bin/env python3

import os
import sys
import yaml
import xml.etree.ElementTree as ET
from typing import Dict, Any

def parse_yaml_to_dict(yaml_path: str) -> Dict[str, Any]:
    with open(yaml_path, 'r') as f:
        data = yaml.safe_load(f)
    return data

def extract_camera_data(cam_data: Dict[str, Any]) -> Dict[str, str]:
    intrinsics = cam_data.get("intrinsics", [0, 0, 0, 0])
    distortion = cam_data.get("distortion_coeffs", [0, 0, 0, 0, 0])
    return {
        "fx": str(intrinsics[0]),
        "fy": str(intrinsics[1]),
        "cx": str(intrinsics[2]),
        "cy": str(intrinsics[3]),
        "k1": str(distortion[0] if len(distortion) > 0 else 0),
        "k2": str(distortion[1] if len(distortion) > 1 else 0),
        "p1": str(distortion[2] if len(distortion) > 2 else 0),
        "p2": str(distortion[3] if len(distortion) > 3 else 0),
        "k3": str(distortion[4] if len(distortion) > 4 else 0),
    }

def extract_name_from_topic(rostopic: str) -> str:
    return rostopic.split('/')[-2].replace('cam_video_', '')

def generate_single_camera_xml(cam_data: Dict[str, Any], name: str, output_dir: str) -> None:
    root = ET.Element("calibrations")
    camera = ET.SubElement(root, "camera")

    camera.set("cameraMaker", "NA")
    camera.set("cameraModel", cam_data.get("camera_model", "pinhole"))
    camera.set("lense", "NA")

    calib = extract_camera_data(cam_data)
    for key, value in calib.items():
        camera.set(key, value)

    camera.set("name", name)

    tree = ET.ElementTree(root)
    output_path = os.path.join(output_dir, f"calibration_{name}.xml")
    tree.write(output_path, encoding="utf-8", xml_declaration=True)
    print(f"✅ Saved: {output_path}")

def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python3 calib_transform.py input.yaml")
        sys.exit(1)

    yaml_path = sys.argv[1]
    if not os.path.exists(yaml_path):
        print(f"❌ File '{yaml_path}' not found.")
        sys.exit(1)

    data = parse_yaml_to_dict(yaml_path)
    output_dir = os.path.dirname(yaml_path) or "."

    for cam_key, cam_data in data.items():
        if not cam_key.startswith("cam"):
            continue

        rostopic = cam_data.get("rostopic", "")
        name = extract_name_from_topic(rostopic)
        generate_single_camera_xml(cam_data, name, output_dir)

if __name__ == "__main__":
    main()
