# solidify.py
# Makes sure a scanned tool is a SOLID silhouette.
#
# Why this exists: the original silhouette step (process.extract_silhouette)
# uses an adaptive threshold. On a large, uniformly dark tool that only picks up
# a band along the tool's edge, and the step then "fills the outline". That is
# fine when the band closes into a loop, but if the band has a gap (shiny edges,
# a light leak) the outline wraps both sides of the band and the tool comes out
# HOLLOW: just a thin ring (plus a separate shape for a slot).
#
# This step leaves the original result alone whenever it is already solid
# (the output is then bit-for-bit what process.extract_silhouette gave). Only
# when the tool is hollow does it add the tool's solid dark interior to the
# original mask and fill it. The original edge is kept; nothing is moved.

from __future__ import annotations

import cv2
import numpy as np

# Pixels this far in from a dark region's edge count as "interior" (px).
INTERIOR_RADIUS_PX = 4
# "Dark" means darker than this fraction of the typical board brightness.
DARK_FRACTION = 0.45
# Frame-edge band ignored (matches process.py's border blanking).
BORDER_PX = 60
# The tool counts as hollow when this much dark interior lies outside the
# original mask (pixels, and as a fraction of the original mask area).
HOLLOW_MIN_PX = 200
HOLLOW_MIN_FRAC = 0.005


def fill_outer(mask: np.ndarray, contour_min_area: float = 0) -> np.ndarray:
    """Fill every outer outline solid (so slots and holes inside a tool close)."""
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    cnts = [c for c in cnts if cv2.contourArea(c) >= contour_min_area]
    out = np.zeros_like(mask)
    cv2.drawContours(out, cnts, -1, 255, thickness=cv2.FILLED)
    return out


def make_solid(image_bgr: np.ndarray, mask: np.ndarray,
               min_area: float = 0) -> tuple[np.ndarray, bool]:
    """Return (solid_mask, changed).

    Args:
        image_bgr: the undistorted, cropped photo the mask came from.
        mask:      uint8 mask from process.extract_silhouette (tool = 255), with
                   NO clearance added yet.
        min_area:  outlines smaller than this (px^2) are ignored.
    """
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    if h <= 2 * BORDER_PX or w <= 2 * BORDER_PX:
        return mask, False
    board = float(np.median(gray[BORDER_PX:-BORDER_PX, BORDER_PX:-BORDER_PX]))
    dark = (gray < DARK_FRACTION * board).astype(np.uint8)
    dark[:BORDER_PX, :] = 0
    dark[-BORDER_PX:, :] = 0
    dark[:, :BORDER_PX] = 0
    dark[:, -BORDER_PX:] = 0

    # Only dark regions that the original mask already touches belong to the tool
    # (this keeps the marker, shadows and other objects out).
    _, labels = cv2.connectedComponents(dark)
    touched = np.unique(labels[mask > 0])
    touched = touched[touched > 0]
    if touched.size == 0:
        return mask, False
    tool_dark = np.isin(labels, touched).astype(np.uint8)

    k = INTERIOR_RADIUS_PX
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * k + 1, 2 * k + 1))
    interior = cv2.erode(tool_dark, kernel)
    original = mask > 0
    missing = int(((interior > 0) & ~original).sum())
    if missing <= max(HOLLOW_MIN_PX, HOLLOW_MIN_FRAC * float(original.sum())):
        return mask, False                      # already solid: leave it exactly as is

    # Hollow: add the tool's solid dark body (reaching the true dark edge, so
    # gaps in the edge band do not leave the outline short), then fill.
    body = cv2.morphologyEx(tool_dark, cv2.MORPH_OPEN, kernel)
    merged = ((original) | (body > 0)).astype(np.uint8) * 255
    merged = cv2.morphologyEx(merged, cv2.MORPH_CLOSE,
                              cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
    return fill_outer(merged, min_area), True
