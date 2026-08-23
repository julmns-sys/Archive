from __future__ import annotations

import cv2
import numpy as np


def _ordered(points: np.ndarray) -> np.ndarray:
    points = np.asarray(points, dtype=np.float32)
    if points.shape != (4, 2):
        raise ValueError("Four corner points are required.")
    result = np.zeros((4, 2), dtype=np.float32)
    sums = points.sum(axis=1)
    differences = np.diff(points, axis=1).reshape(-1)
    result[0] = points[np.argmin(sums)]
    result[2] = points[np.argmax(sums)]
    result[1] = points[np.argmin(differences)]
    result[3] = points[np.argmax(differences)]
    return result


def perspective_correct(image: np.ndarray, points: np.ndarray) -> np.ndarray:
    top_left, top_right, bottom_right, bottom_left = _ordered(points)
    width = max(np.linalg.norm(bottom_right - bottom_left), np.linalg.norm(top_right - top_left))
    height = max(np.linalg.norm(top_right - bottom_right), np.linalg.norm(top_left - bottom_left))
    output_width, output_height = int(round(width)), int(round(height))
    if output_width < 20 or output_height < 20:
        raise ValueError("The crop area is too small.")
    destination = np.array(
        [[0, 0], [output_width - 1, 0], [output_width - 1, output_height - 1], [0, output_height - 1]],
        dtype=np.float32,
    )
    transform = cv2.getPerspectiveTransform(np.array([top_left, top_right, bottom_right, bottom_left]), destination)
    return cv2.warpPerspective(image, transform, (output_width, output_height), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)

