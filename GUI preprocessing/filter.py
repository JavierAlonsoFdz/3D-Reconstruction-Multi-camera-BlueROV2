from __future__ import annotations
import cv2
import numpy as np
import numpy.typing as npt
from typing import Tuple


# ------------------- Basic stats / helpers -------------------

def compute_colorfulness(img_bgr: npt.NDArray[np.uint8]) -> float:
    """Hasler & Süsstrunk colorfulness metric."""
    rg = img_bgr[:, :, 2].astype(np.float32) - img_bgr[:, :, 1].astype(np.float32)
    yb = 0.5 * (img_bgr[:, :, 2].astype(np.float32) + img_bgr[:, :, 1].astype(np.float32)) - img_bgr[:, :, 0].astype(np.float32)
    std_rg = float(np.std(rg))
    std_yb = float(np.std(yb))
    mean_rg = float(np.mean(rg))
    mean_yb = float(np.mean(yb))
    return float(np.sqrt(std_rg**2 + std_yb**2) + 0.3 * np.sqrt(mean_rg**2 + mean_yb**2))

def adjust_saturation(img_bgr: npt.NDArray[np.uint8], scale: float) -> npt.NDArray[np.uint8]:
    """Scale saturation in HSV (safe clipping)."""
    hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV).astype(np.float32)
    hsv[:, :, 1] *= float(scale)
    hsv[:, :, 1] = np.clip(hsv[:, :, 1], 0, 255)
    return cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)


# ------------------- Turbid CLAHE filter -------------------

def gentle_color_restore(img: npt.NDArray[np.uint8]) -> npt.NDArray[np.uint8]:
    """
    Turbid-water preprocessing for extraction/reconstruction:
      1) Gray-world white balance to reduce green/blue cast
      2) Convert BGR to L*a*b*
      3) Apply per-frame CLAHE only on the L channel
      4) Convert back to BGR
      5) Apply mild unsharp masking
    """
    imgf = img.astype(np.float32)
    avg_b = imgf[:, :, 0].mean()
    avg_g = imgf[:, :, 1].mean()
    avg_r = imgf[:, :, 2].mean()
    avg_gray = float((avg_b + avg_g + avg_r) / 3.0 + 1e-9)

    scale_b = avg_gray / (avg_b + 1e-9)
    scale_g = avg_gray / (avg_g + 1e-9)
    scale_r = avg_gray / (avg_r + 1e-9)

    wb = imgf.copy()
    wb[:, :, 0] *= float(scale_b)
    wb[:, :, 1] *= float(scale_g)
    wb[:, :, 2] *= float(scale_r)
    wb = np.clip(wb, 0, 255).astype(np.uint8)

    lab = cv2.cvtColor(wb, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    l2 = clahe.apply(l)
    clahe_out = cv2.cvtColor(cv2.merge((l2, a, b)), cv2.COLOR_LAB2BGR)

    blur = cv2.GaussianBlur(clahe_out, (0, 0), 1.0)
    return cv2.addWeighted(clahe_out, 1.0, blur, -0.20, 0)

# ------------------- Clear water helpers (detail + sharpen) --------------

def sharpen_image(img: npt.NDArray[np.uint8]) -> npt.NDArray[np.uint8]:
    """
    Much more conservative sharpening kernel than before.
    Reduces the overall brightening/halo effect.
    """
    # Kernel with weaker central gain -> milder sharpening
    kernel = np.array([
        [0.0,  -0.25, 0.0],
        [-0.25, 1.5, -0.25],
        [0.0,  -0.25, 0.0]
    ], dtype=np.float32)
    return cv2.filter2D(img, -1, kernel)


def enhance_detail(img: npt.NDArray[np.uint8], sigma_s: float = 4.0, sigma_r: float = 0.08) -> npt.NDArray[np.uint8]:
    """
    Gentle detail enhancement using OpenCV detailEnhance with softer params.
    Lower sigma_s reduces the spatial smoothing, lower sigma_r reduces range
    amplification — both make the result subtler.
    """
    # Use conservative parameters to avoid over-brightening and halos
    return cv2.detailEnhance(img, sigma_s=float(sigma_s), sigma_r=float(sigma_r))


def adaptive_blend(orig: npt.NDArray[np.uint8], proc: npt.NDArray[np.uint8], strength: float) -> npt.NDArray[np.uint8]:
    """
    Blend processed and original: alpha=strength (proc*alpha + orig*(1-alpha))
    Strength closer to 0 => mostly original, closer to 1 => mostly processed.
    """
    alpha = float(np.clip(strength, 0.0, 1.0))
    return cv2.addWeighted(proc, alpha, orig, 1.0 - alpha, 0.0)


def enhance_clear_water(img: npt.NDArray[np.uint8], strength: float = 0.25) -> npt.NDArray[np.uint8]:
    """
    Clear-water pipeline (conservative):
      - detailEnhance with soft params
      - mild sharpening with conservative kernel
      - blend with original using low default strength (0.25)

    Parameters:
        img: BGR image (uint8)
        strength: how strong to apply the processed result (0..1). Default 0.25 (very subtle).

    Returns:
        final BGR image uint8
    """
    # 1) gentle detail enhancement
    det = enhance_detail(img, sigma_s=4.0, sigma_r=0.08)

    # 2) very mild sharpening
    sh = sharpen_image(det)

    # 3) blend conservatively with original to avoid excessive brightening
    final = adaptive_blend(img, sh, float(np.clip(strength, 0.0, 1.0)))
    return final
