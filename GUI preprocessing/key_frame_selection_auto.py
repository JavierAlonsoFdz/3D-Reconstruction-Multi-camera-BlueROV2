from __future__ import annotations
import cv2
import numpy as np
import numpy.typing as npt
from typing import Dict, List, Tuple, Union


RATIO_TEST = 0.75
RANSAC_THRESH = 2.8
MIN_FRAME_GAP = 8
MAX_GAP_FRAMES = 60


# ------------------- ORB/RANSAC Matching -------------------

def compute_inlier_ratio(
    gray_ref: npt.NDArray[np.uint8],
    gray_cur: npt.NDArray[np.uint8],
    *,
    ratio_test: float,
    ransac_thresh: float,
    nfeatures: int = 3500,
    fast_thr: int = 6
) -> Tuple[int, float, int]:
    """
    Computes the RANSAC inlier ratio r_ir = inliers / matches.
    This is not a direct measure of spatial image overlap.

    Returns:
        inliers: number of inlier matches
        inlier_ratio: inliers / good_matches
        good_matches: matches supplied to RANSAC after Lowe's ratio test
    """
    orb = cv2.ORB_create(nfeatures=nfeatures, fastThreshold=fast_thr)
    k0, d0 = orb.detectAndCompute(gray_ref, None)
    k1, d1 = orb.detectAndCompute(gray_cur, None)

    if d0 is None or d1 is None or len(k0) == 0 or len(k1) == 0:
        return 0, 0.0, 0

    bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
    matches_knn = bf.knnMatch(d0, d1, k=2)

    good = []
    for pair in matches_knn:
        if len(pair) < 2:
            continue
        m, n = pair
        if m.distance < ratio_test * n.distance:
            good.append(m)
    if len(good) < 8:
        return 0, 0.0, len(good)

    pts0 = np.float32([k0[m.queryIdx].pt for m in good]).reshape(-1, 2)
    pts1 = np.float32([k1[m.trainIdx].pt for m in good]).reshape(-1, 2)

    if pts0.shape[0] != pts1.shape[0] or pts0.shape[0] < 8:
        return 0, 0.0, len(good)

    try:
        F, mask = cv2.findFundamentalMat(
            pts0, pts1, cv2.FM_RANSAC, ransac_thresh, 0.999
        )
    except cv2.error:
        return 0, 0.0, len(good)

    if F is None or mask is None:
        return 0, 0.0, len(good)

    inliers = int(mask.sum())
    inlier_ratio = inliers / max(len(good), 1)
    return inliers, inlier_ratio, len(good)


# ------------------- KF Decision Rule -------------------

def should_make_kf(
    disp_est: float,
    inliers: int,
    inlier_ratio: float,
    since_last_kf: int,
    *,
    min_disp_px: float,
    min_inliers: int,
    inlier_ratio_thresh: float,
    max_gap_frames: int
) -> Tuple[bool, str]:
    """
    Minimal OR-rule keyframe policy.
    1) Enough parallax, OR
    2) Low inlier ratio, OR
    3) Few inliers (weak geometry), OR
    4) Safety GAP.
    Returns (make_kf, cause).
    """

    if disp_est >= min_disp_px:
        return True, f"parallax (disp {disp_est:.1f} >= {min_disp_px})"

    if inlier_ratio <= inlier_ratio_thresh:
        return True, f"inlier_ratio (r_ir {inlier_ratio:.2f} <= {inlier_ratio_thresh})"

    if inliers < min_inliers:
        return True, f"inliers (N {inliers} < {min_inliers})"

    if since_last_kf >= max_gap_frames:
        return True, f"gap (delta_k {since_last_kf} >= {max_gap_frames})"

    return False, ""


# ------------------- Keyframe Selection -------------------

def compute_keyframes_by_flow_sparse_simple(
    video_path: str,
    *,
    min_disp_px: float,
    min_inliers: int,
    inlier_ratio_thresh: float,
    return_reasons: bool = False
) -> Union[List[int], Tuple[List[int], Dict[int, str]]]:
    """
    Keyframe extraction using:
      - Sparse optical flow (LK)
      - ORB + RANSAC
      - Minimal OR-rule for KF decisions

    Returns:
        Sorted list of keyframe indices.
        If return_reasons=True, also returns a frame-index to reason mapping.
    """
    resize_width = 640
    scan_step = 2
    max_corners = 1500
    quality = 0.01
    min_dist = 8

    lk_win = (21, 21)
    lk_lvl = 2
    lk_crit = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 20, 0.03)

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open {video_path}")

    def to_gray_small(img: npt.NDArray[np.uint8]) -> npt.NDArray[np.uint8]:
        h, w = img.shape[:2]
        if w > resize_width:
            scale = resize_width / float(w)
            img = cv2.resize(img, (resize_width, int(h * scale)), interpolation=cv2.INTER_AREA)
        return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    ret, frame0 = cap.read()
    if not ret:
        return []

    gray0_full = cv2.cvtColor(frame0, cv2.COLOR_BGR2GRAY)
    gray0 = to_gray_small(frame0)

    kf_list: List[int] = [0]
    kf_reasons: Dict[int, str] = {0: "initial frame"}
    last_kf = 0
    frame_idx = 0

    p0 = cv2.goodFeaturesToTrack(gray0, maxCorners=max_corners, qualityLevel=quality, minDistance=min_dist)

    force_scan = True

    try:
        while True:
            local_step = 1 if force_scan else scan_step
            force_scan = False

            ret = False
            frame = None
            for _ in range(local_step):
                ret, frame = cap.read()
                if not ret:
                    break
                frame_idx += 1

            if not ret or frame is None:
                break

            gray = to_gray_small(frame)

            # reseed features if necessary
            if p0 is None or len(p0) < 200:
                p0 = cv2.goodFeaturesToTrack(gray0, maxCorners=max_corners, qualityLevel=quality, minDistance=min_dist)
                if p0 is None:
                    gray0 = gray
                    continue

            # LK flow
            p1, st, _ = cv2.calcOpticalFlowPyrLK(gray0, gray, p0, None, winSize=lk_win, maxLevel=lk_lvl, criteria=lk_crit)
            if p1 is None or st is None:
                continue

            good_new = p1[st == 1]
            good_old = p0[st == 1]
            if len(good_new) < 50:
                continue

            disp = np.linalg.norm(good_new - good_old, axis=1)
            disp_small = float(np.median(disp))
            scale = gray0_full.shape[1] / float(gray0.shape[1])
            disp_est = disp_small * scale

            # ORB/RANSAC
            inl, inlier_ratio, gm = compute_inlier_ratio(gray0, gray, ratio_test=RATIO_TEST, ransac_thresh=RANSAC_THRESH)

            since_last = frame_idx - last_kf
            make_kf, cause = should_make_kf(
                disp_est, inl, inlier_ratio, since_last,
                min_disp_px=min_disp_px,
                min_inliers=min_inliers,
                inlier_ratio_thresh=inlier_ratio_thresh,
                max_gap_frames=MAX_GAP_FRAMES
            )

            if disp_est >= 0.8 * min_disp_px or inl < int(min_inliers * 1.2):
                force_scan = True

            if make_kf:
                kf_list.append(frame_idx)
                kf_reasons[frame_idx] = cause
                gray0_full = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                gray0 = gray
                p0 = cv2.goodFeaturesToTrack(gray0, maxCorners=max_corners, qualityLevel=quality, minDistance=min_dist)
                last_kf = frame_idx
                print(f"[KF] idx={frame_idx} | cause={cause} | disp={disp_est:.1f}px | "
                      f"inliers={inl} | inlier_ratio={inlier_ratio:.2f} | matches={gm}")
            elif frame_idx % 300 == 0:
                print(f"[info] f={frame_idx} disp={disp_est:.1f}px inliers={inl} "
                      f"inlier_ratio={inlier_ratio:.2f} lastKF={last_kf}")

            p0 = good_new.reshape(-1, 1, 2)



    except KeyboardInterrupt:
        print("[Interrupt] Partial keyframes returned.")

    cap.release()
    keyframes = sorted(set(kf_list))
    if return_reasons:
        return keyframes, {k: kf_reasons.get(k, "") for k in keyframes}
    return keyframes


# ------------------- Thinning -------------------

def thin_keyframes(kfs: List[int], min_frame_gap: int = MIN_FRAME_GAP) -> List[int]:
    """
    Ensures a minimum frame gap between consecutive keyframes.
    """
    if not kfs:
        return kfs
    out: List[int] = [kfs[0]]
    last = kfs[0]

    for k in sorted(kfs)[1:]:
        if k - last >= min_frame_gap:
            out.append(k)
            last = k

    return out
