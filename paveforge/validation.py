"""Vectorized topology and geometry checks; no per-edge GEOS query loop."""
import numpy as np
import shapely

from .model import Mesh, MeshError, edge_precision
from .quality import quality_report


def polygon_parts(geometry):
    if geometry.is_empty:
        return []
    if geometry.geom_type == "Polygon":
        return [geometry]
    if geometry.geom_type in ("MultiPolygon", "GeometryCollection"):
        return [p for g in geometry.geoms for p in polygon_parts(g)]
    return []


def validate_mesh(mesh: Mesh, domain, tolerance: float, *, angle_min=5., angle_max=160.) -> dict:
    if not mesh.cells:
        raise MeshError("Mesher returned no elements.")
    polys = np.empty(len(mesh.cells), dtype=object)
    all_edges, all_owners = [], []
    for size in (3, 4):
        selection = np.array([i for i, c in enumerate(mesh.cells) if len(c.nodes) == size], dtype=int)
        if not len(selection):
            continue
        connectivity = np.array([mesh.cells[i].nodes for i in selection])
        coords = mesh.nodes[connectivity, :2]
        polys[selection] = shapely.polygons(coords)
        edges = np.stack((connectivity, np.roll(connectivity, -1, axis=1)), axis=2)
        all_edges.append(np.sort(edges.reshape(-1, 2), axis=1))
        all_owners.append(np.repeat(selection, size))
    areas = shapely.area(polys)
    if not shapely.is_valid(polys).all() or (areas <= 0).any():
        raise MeshError("Mesher produced a degenerate or self-intersecting element.")
    edges = np.concatenate(all_edges)
    owner = np.concatenate(all_owners)
    if len(np.unique(edges)) != len(mesh.nodes):
        raise MeshError("Mesh has unreferenced nodes.")
    points = shapely.points(mesh.nodes[:, :2])
    tree = shapely.STRtree(points)
    first, second = tree.query(points, predicate="dwithin", distance=tolerance)
    if (first != second).any():
        raise MeshError(f"Mesh has duplicate/near-duplicate XY nodes within {tolerance:g} m. No mesh may be exported.")
    unique, inverse, counts = np.unique(edges, axis=0, return_inverse=True, return_counts=True)
    if (counts > 2).any():
        raise MeshError("Nonmanifold edge: more than two elements share an edge.")
    parents = list(range(len(mesh.cells)))

    def root(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i

    order = np.argsort(inverse, kind="stable")
    offsets = np.r_[0, np.cumsum(counts)[:-1]][counts == 2]
    for a, b in zip(owner[order[offsets]], owner[order[offsets + 1]]):
        parents[root(int(b))] = root(int(a))
    components = len({root(i) for i in range(len(mesh.cells))})
    expected = len(polygon_parts(domain))
    if components != expected:
        raise MeshError(f"Mesh has {components} disconnected element regions; expected {expected} from basepoly.")
    line_geometries = shapely.linestrings(mesh.nodes[unique, :2])
    precision = edge_precision(mesh.nodes)
    edge_index, node_index = tree.query(line_geometries, predicate="dwithin", distance=precision)
    endpoints = unique[edge_index]
    if ((node_index != endpoints[:, 0]) & (node_index != endpoints[:, 1])).any():
        raise MeshError("Hanging node detected on an element edge.")
    # Use the fast coverage union only after verifying edge matching/nonoverlap.
    fast = bool(shapely.coverage_is_valid(polys))
    covered = shapely.coverage_union_all(polys) if fast else shapely.union_all(polys)
    area_tolerance = max(domain.area * 1e-9, tolerance * domain.length * 4)
    overlap = max(0., float(areas.sum()) - covered.area)
    gap = domain.difference(covered).area
    outside = covered.difference(domain).area
    expected_holes = sum(len(p.interiors) for p in polygon_parts(domain))
    actual_holes = sum(len(p.interiors) for p in polygon_parts(covered))
    if actual_holes != expected_holes:
        raise MeshError(f"Coverage has {actual_holes:,} holes; expected {expected_holes:,}. Unintended voids or lost exclusions are not permitted.")
    if max(overlap, gap, outside) > area_tolerance:
        raise MeshError(f"Coverage validation failed: gap={gap:g}, overlap={overlap:g}, outside={outside:g}.")
    paving = np.array([not c.structure for c in mesh.cells])
    quality = quality_report(mesh, angle_min, angle_max)
    return {
        "nodes": len(mesh.nodes), "elements": len(mesh.cells),
        "paving_triangles": sum(not c.structure and len(c.nodes) == 3 for c in mesh.cells),
        "structure_elements": sum(c.structure for c in mesh.cells),
        "connected_components": components, "expected_components": expected,
        "duplicate_nodes": 0, "hanging_nodes": 0, "unused_nodes": 0,
        "gap_area": gap, "overlap_area": overlap, "outside_area": outside,
        "domain_area": domain.area, "area_validation_tolerance": area_tolerance,
        "intentional_holes": expected_holes, "covered_holes": actual_holes,
        "max_paving_area": float(areas[paving].max()) if paving.any() else 0.,
        "minimum_paving_angle": quality["minimum_paving_angle"], "vectorized_coverage": fast,
        "quality": quality,
        "duplicate_check_tolerance": tolerance,
        "edge_collinearity_precision": precision,
    }
