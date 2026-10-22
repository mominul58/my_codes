"""Stable interior-angle measurements and export acceptance for every cell."""
import csv
from io import StringIO

import numpy as np

from .model import MeshError


def check_angle_limits(minimum, maximum):
    if not np.isfinite([minimum, maximum]).all() or not 0 < minimum < maximum < 180:
        raise MeshError("Angle limits must satisfy 0 < minimum < maximum < 180 degrees.")


def interior_angles(xy):
    """Include reflex angles, use atan2, and avoid global-coordinate cancellation."""
    previous = np.roll(xy, 1, axis=-2) - xy
    following = np.roll(xy, -1, axis=-2) - xy
    cross = previous[..., 0] * following[..., 1] - previous[..., 1] * following[..., 0]
    angle = np.degrees(np.arctan2(np.abs(cross), np.sum(previous * following, axis=-1)))
    local = xy - xy[..., :1, :]
    orientation = np.sum(local[..., 0] * np.roll(local[..., 1], -1, axis=-1)
                         - local[..., 1] * np.roll(local[..., 0], -1, axis=-1), axis=-1)
    return np.where(cross * orientation[..., None] > 0, 360 - angle, angle)


def cell_angle_extrema(mesh):
    low, high = np.empty(len(mesh.cells)), np.empty(len(mesh.cells))
    for size in (3, 4):
        selection = np.asarray([i for i, c in enumerate(mesh.cells) if len(c.nodes) == size], dtype=int)
        if len(selection):
            connectivity = np.asarray([mesh.cells[i].nodes for i in selection])
            angles = interior_angles(mesh.nodes[connectivity, :2])
            low[selection], high[selection] = angles.min(axis=1), angles.max(axis=1)
    return low, high


def quality_report(mesh, minimum=5., maximum=160.):
    from .formats import export_ids
    check_angle_limits(minimum, maximum)
    low, high = cell_angle_extrema(mesh)
    below, above = low < minimum - 1e-7, high > maximum + 1e-7
    failed = below | above
    source = np.asarray([c.structure for c in mesh.cells])
    _, element_ids = export_ids(mesh)
    return {
        "minimum_allowed_angle": minimum, "maximum_allowed_angle": maximum,
        "all_elements_checked": len(mesh.cells), "angles_passed": not bool(failed.any()),
        "minimum_element_angle": float(low.min()), "maximum_element_angle": float(high.max()),
        "elements_below_minimum_angle": int(below.sum()),
        "elements_above_maximum_angle": int(above.sum()),
        "elements_failing_angles": int(failed.sum()),
        "structure_elements_failing_angles": int((failed & source).sum()),
        "paving_elements_failing_angles": int((failed & ~source).sum()),
        "minimum_paving_angle": float(low[~source].min()) if (~source).any() else None,
        "angle_failure_examples": [
            {"element_id": int(element_ids[i]), "structure": mesh.cells[i].structure,
             "minimum_angle": float(low[i]), "maximum_angle": float(high[i])}
            for i in np.flatnonzero(failed)[:50]],
    }


def angle_failures_csv(mesh):
    """Use actual exported IDs, so diagnostics locate the same cells in SMS."""
    from .formats import export_ids
    low, high = cell_angle_extrema(mesh)
    limits = mesh.report.get("quality", {})
    minimum, maximum = limits.get("minimum_allowed_angle", 5.), limits.get("maximum_allowed_angle", 160.)
    failed = (low < minimum - 1e-7) | (high > maximum + 1e-7)
    _, ids = export_ids(mesh)
    out = StringIO()
    writer = csv.writer(out)
    writer.writerow(["element_id", "source", "node_ids", "material", "minimum_angle", "maximum_angle", "centroid_x", "centroid_y"])
    node_ids, _ = export_ids(mesh)
    for i in np.flatnonzero(failed):
        cell = mesh.cells[i]
        center = mesh.nodes[list(cell.nodes), :2].mean(axis=0)
        writer.writerow([ids[i], "structure" if cell.structure else "paving",
                         " ".join(str(node_ids[n]) for n in cell.nodes), cell.material,
                         format(low[i], ".10g"), format(high[i], ".10g"), *center])
    return out.getvalue()
