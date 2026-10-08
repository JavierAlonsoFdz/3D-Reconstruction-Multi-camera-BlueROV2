import sys
import pandas as pd
import numpy as np
import json
import os
from pyproj import Transformer


def latlon_to_ecef(lat: float, lon: float, height: float) -> np.ndarray:
    """
    Convert WGS84 (lat, lon, height) to ECEF (X, Y, Z).
    """
    transformer = Transformer.from_crs("EPSG:4979", "EPSG:4978", always_xy=True)
    x, y, z = transformer.transform(lon, lat, height)
    return np.array([x, y, z], dtype=np.float64)


def generate_transform_matrix(translation: np.ndarray) -> np.ndarray:
    """
    Generate a 4x4 transformation matrix with only translation (no rotation/scaling).
    """
    mat = np.eye(4)
    mat[:3, 3] = translation
    return mat


def _normalize_col(name: str) -> str:
    return "".join(ch for ch in str(name).strip().lower() if ch.isalnum())


def _find_column(df: pd.DataFrame, aliases: list[str]) -> str | None:
    normalized_to_original = {_normalize_col(c): c for c in df.columns}
    for alias in aliases:
        col = normalized_to_original.get(_normalize_col(alias))
        if col:
            return col
    return None


def read_geodetic_from_csv(csv_path: str) -> tuple[float, float, float]:
    """
    Read latitude, longitude and height from a CSV.
    Accepted columns:
    - latitude: Latitude, lat
    - longitude: Longitude, lon, lng
    - height: Height, Altitude, Elevation, Z
    - or depth: Depth (positive down). Then height = -Depth.
    """
    df = pd.read_csv(csv_path)
    if df.empty:
        raise ValueError(f"CSV is empty: {csv_path}")

    lat_col = _find_column(df, ["Latitude", "lat"])
    lon_col = _find_column(df, ["Longitude", "lon", "lng"])
    height_col = _find_column(df, ["Height", "Altitude", "Elevation", "Z"])
    depth_col = _find_column(df, ["Depth"])

    if not lat_col or not lon_col:
        raise ValueError(
            "Could not find Latitude/Longitude columns in CSV. "
            "Expected names like Latitude, Longitude, lat, lon."
        )

    lat = float(df.iloc[0][lat_col])
    lon = float(df.iloc[0][lon_col])

    if height_col:
        height = float(df.iloc[0][height_col])
    elif depth_col:
        height = -float(df.iloc[0][depth_col])  # Depth is positive down.
    else:
        raise ValueError(
            "Could not find Height/Altitude/Elevation/Z or Depth column in CSV."
        )

    return lat, lon, height


def create_tileset(mesh_glb_filename: str, transform_matrix: np.ndarray, output_path: str):
    """
    Create tileset.json with a single GLB mesh and pure translation transform.
    """
    tileset = {
        "asset": {
            "version": "1.1"
        },
        "extensionsUsed": ["3DTILES_content_gltf"],
        "extensionsRequired": ["3DTILES_content_gltf"],
        "geometricError": 500,
        "root": {
            "transform": transform_matrix.T.flatten().tolist(),  # Column-major order
            "boundingVolume": {
                "box": [
                    0, 0, 0,
                    40, 0, 0,
                    0, 40, 0,
                    0, 0, 40
                ]
            },
            "geometricError": 0,
            "refine": "ADD",
            "content": {
                "uri": os.path.basename(mesh_glb_filename)
            }
        }
    }

    with open(output_path, "w") as f:
        json.dump(tileset, f, indent=2)
    print(f"tileset.json written to {output_path}")


def create_focus_point_config(
    csv_path: str,
    mesh_glb_filename: str,
    output_path: str,
    switch_distance_m: float = 2000.0,
    flyto_range_m: float = 120.0,
):
    """
    Generate a Cesium-friendly config that separates:
    - map localization (point marker at long distance)
    - model detail (show model only when camera is near)

    This config is intended for the frontend where:
    - marker is visible from switch_distance_m to far distance
    - model is visible from near distance to switch_distance_m
    - click on marker -> flyTo model destination
    """
    lat, lon, height = read_geodetic_from_csv(csv_path)

    config = {
        "name": os.path.splitext(os.path.basename(mesh_glb_filename))[0],
        "position": {
            "latitude": lat,
            "longitude": lon,
            "height": height,
        },
        "model": {
            "uri": os.path.basename(mesh_glb_filename),
            "distanceDisplayCondition": {
                "near": 0.0,
                "far": float(switch_distance_m),
            },
        },
        "marker": {
            "type": "point",
            "pixelSize": 12,
            "color": "#00E5FF",
            "outlineColor": "#003344",
            "outlineWidth": 2,
            "distanceDisplayCondition": {
                "near": float(switch_distance_m),
                "far": 20000000.0,
            },
            "onClick": "flyToModel",
        },
        "flyTo": {
            "destination": {
                "latitude": lat,
                "longitude": lon,
                "height": max(height + float(flyto_range_m), 5.0),
            },
            "rangeMeters": float(flyto_range_m),
        },
    }

    with open(output_path, "w") as f:
        json.dump(config, f, indent=2)
    print(f"focus config written to {output_path}")


def main():
    if len(sys.argv) not in (4, 5):
        print(
            "Usage: python geolocate_glb.py <model.glb> <input.csv> <output_tileset.json> "
            "[output_focus_config.json]"
        )
        sys.exit(1)

    glb_path = sys.argv[1]
    csv_path = sys.argv[2]
    output_tileset_json = sys.argv[3]
    output_focus_json = sys.argv[4] if len(sys.argv) == 5 else None

    lat, lon, height = read_geodetic_from_csv(csv_path)

    ecef = latlon_to_ecef(lat, lon, height)
    transform_matrix = generate_transform_matrix(ecef)
    create_tileset(glb_path, transform_matrix, output_tileset_json)

    if output_focus_json:
        create_focus_point_config(
            csv_path=csv_path,
            mesh_glb_filename=glb_path,
            output_path=output_focus_json,
        )


if __name__ == "__main__":
    main()
