"""Align polygon vertices to immutable structure edges within an explicit distance."""
from dataclasses import replace

import numpy as np
import shapely

from .basepoly import BasePoly
from .model import MeshError


def align_basepoly(basepoly, structure_union, distance):
    if not np.isfinite(distance) or distance < 0:
        raise MeshError("Boundary cleanup distance must be nonnegative metres.")
    if distance == 0 or structure_union.is_empty:
        return basepoly, {"distance_m": distance, "changed_features": 0, "symmetric_difference_area": 0.}
    segments = []
    for ring in shapely.get_parts(structure_union.boundary):
        xy = shapely.get_coordinates(ring)
        segments.extend(np.stack((xy[:-1], xy[1:]), axis=1))
    segments = np.asarray(segments)
    tree = shapely.STRtree(shapely.linestrings(segments))
    geoms = np.asarray([r.polygon for r in basepoly.regions], dtype=object)
    original = shapely.get_coordinates(geoms)
    mapped = original.copy()
    hits, distances = tree.query_nearest(shapely.points(original), max_distance=distance, all_matches=False, return_distance=True)
    # Existing base meshes usually already share most polygon boundaries. Avoid
    # resnapping those exact matches to nearby subdivision vertices: it adds work
    # and can damage a small exclusion that was already correctly aligned.
    coincidence = max(1e-10, float(np.spacing(np.abs(original).max())) * 8)
    hit_points, hit_edges = hits
    endpoint_distance = np.linalg.norm(original[hit_points, None, :] - segments[hit_edges], axis=2).min(axis=1)
    move = (distances > coincidence) | ((endpoint_distance > coincidence) & (endpoint_distance <= distance))
    point_ids, edge_ids = hits[:, move]
    if len(point_ids):
        edges = segments[edge_ids]
        vector = edges[:, 1] - edges[:, 0]
        fractions = np.clip(np.sum((original[point_ids] - edges[:, 0]) * vector, axis=1) / np.sum(vector**2, axis=1), 0, 1)
        mapped[point_ids] = edges[:, 0] + fractions[:, None] * vector
    cleaned = shapely.set_coordinates(geoms.copy(), mapped)
    changed, warnings, output, skipped = 0, list(basepoly.warnings), [], []
    offset = 0
    for region, geometry in zip(basepoly.regions, cleaned):
        count = len(shapely.get_coordinates(region.polygon))
        selection = (point_ids >= offset) & (point_ids < offset + count)
        refs = segments[edge_ids[selection]].reshape(-1, 2)
        # Include the nearest edge endpoints when appropriate; this aligns
        # almost-coincident corners and inserts immutable reference vertices.
        if len(refs):
            geometry = shapely.snap(geometry, shapely.MultiPoint(np.unique(refs, axis=0)), distance)
        offset += count
        if not geometry.is_valid or geometry.is_empty or geometry.area <= 0:
            # Cleanup is optional. Keep the exact original feature when a
            # proposed alignment would damage it; exclusions must never vanish.
            geometry = region.polygon
            skipped.append(region.feature)
        if not geometry.equals_exact(region.polygon, 0):
            changed += 1
        output.append(replace(region, polygon=geometry))
    result = BasePoly(output, warnings)
    delta = basepoly.domain.symmetric_difference(result.domain).area
    if changed:
        warnings.append(f"Aligned {changed:,} basepoly parts to fixed structure boundaries within {distance:g} m; changed paving footprint by {delta:.6f} m². Original structure node XYZ values remain unchanged.")
    if skipped:
        warnings.append(f"Kept {len(skipped):,} original basepoly parts because boundary cleanup would invalidate them. Skipped feature IDs are in the cleanup report.")
    return result, {"distance_m": distance, "changed_features": changed, "symmetric_difference_area": delta,
                    "original_paving_area": basepoly.domain.area, "cleaned_paving_area": result.domain.area,
                    "skipped_feature_ids": skipped}
