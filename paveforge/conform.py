"""Repair only affected source cells; query all original edges in one batch."""
from dataclasses import replace
import numpy as np
import shapely
import triangle

from .model import MeshError
from .quality import interior_angles


def conform_sources(index, cells, source_count, tolerance, *, minimum_angle=0.,
                    angle_min=5., angle_max=160., max_elements=2_000_000, progress=None):
    if not cells:
        return [], 0
    edge_arrays, owner_arrays, cell_arrays = [], [], []
    for size in (3, 4):
        selection = np.asarray([i for i, c in enumerate(cells) if len(c.nodes) == size], dtype=int)
        if not len(selection):
            continue
        ids = np.asarray([cells[i].nodes for i in selection])
        cell_arrays.append((size, selection, ids))
        edge_arrays.append(np.sort(np.stack([ids, np.roll(ids, -1, axis=1)], axis=2).reshape(-1, 2), axis=1))
        owner_arrays.append(np.repeat(selection, size))
    unique, inverse = np.unique(np.concatenate(edge_arrays), axis=0, return_inverse=True)
    owners = np.concatenate(owner_arrays)
    xyz = np.asarray(index.nodes)
    announce = progress or (lambda message: None)
    announce(f"Checking {len(unique):,} source edges against {len(xyz):,} shared nodes")
    point_tree = shapely.STRtree(shapely.points(xyz[:, :2]))
    edges = shapely.linestrings(xyz[unique, :2])
    edge_hit, node_hit = point_tree.query(edges, predicate="dwithin", distance=tolerance * .25)
    announce(f"Found {len(edge_hit):,} source-edge/node contacts")
    ends = unique[edge_hit]
    interior = (node_hit != ends[:, 0]) & (node_hit != ends[:, 1])
    splits = {}
    assigned = set(range(source_count))
    for edge, node in zip(edge_hit[interior], node_hit[interior]):
        a, b = unique[edge]
        vector = xyz[b, :2] - xyz[a, :2]
        fraction = float(np.dot(xyz[node, :2] - xyz[a, :2], vector) / np.dot(vector, vector))
        z = xyz[a, 2] + fraction * (xyz[b, 2] - xyz[a, 2])
        if int(node) in assigned:
            if abs(index.nodes[node][2] - z) > tolerance:
                raise MeshError("Structure edges meet at conflicting elevations.")
        else:
            x, y, _ = index.nodes[node]
            index.nodes[node] = (x, y, float(z))
            assigned.add(int(node))
        splits.setdefault(tuple(unique[edge]), []).append((fraction, int(node)))
    affected_edges = {i for i, edge in enumerate(unique) if tuple(edge) in splits}
    affected = set(map(int, owners[np.isin(inverse, list(affected_edges))]))
    # Existing convex quads and triangles remain intact, including their IDs.
    for size, selection, ids in cell_arrays:
        xy = xyz[ids, :2]
        if minimum_angle:
            angles = interior_angles(xy)
            bad = (angles.min(axis=1) < angle_min - 1e-7) | (angles.max(axis=1) > angle_max + 1e-7)
            affected.update(map(int, selection[bad]))
        if size == 4:
            delta = np.roll(xy, -1, axis=1) - xy
            turn = delta[..., 0] * np.roll(delta[..., 1], -1, axis=1) - delta[..., 1] * np.roll(delta[..., 0], -1, axis=1)
            concave = ~((turn >= 0).all(axis=1) | (turn <= 0).all(axis=1))
            affected.update(map(int, selection[concave]))
    output = []
    announce(f"Repairing {len(affected):,} source cells; local refinement is bounded")
    next_id = max((c.id or 0 for c in cells), default=0) + 1
    for i, cell in enumerate(cells):
        if i not in affected:
            output.append(cell)
            continue
        ring = []
        for a, b in zip(cell.nodes, cell.nodes[1:] + cell.nodes[:1]):
            ring.append(a)
            key = tuple(sorted((a, b)))
            entries = splits.get(key, [])
            ring.extend(n for _, n in sorted(entries, reverse=a > b))
        local = np.asarray([index.nodes[n][:2] for n in ring])
        segments = np.stack([np.arange(len(ring)), np.roll(np.arange(len(ring)), -1)], axis=1)
        quality = f"q{minimum_angle:g}" if minimum_angle else ""
        if len(ring) == 3 and interior_angles(local).min() < angle_min - 1e-7:
            # An acute immutable triangle corner cannot be enlarged by adding
            # interior vertices. Preserve it for diagnostics instead of creating
            # more failing children.
            output.append(cell)
            continue
        # Pathological fixed corners must not consume the entire job budget in
        # one source cell. A bounded local repair can fail angle acceptance;
        # it must still return quickly enough to provide useful diagnostics.
        local_budget = min(max_elements - len(output), 32)
        if local_budget <= 0:
            raise MeshError("Source connectivity repair exceeds the element budget.")
        if not shapely.Polygon(local).is_valid:
            raise MeshError(f"Source element {cell.id or i + 1} has a non-simple interface after welding. Reduce the node merge distance.")
        origin = local[0].copy()
        result = triangle.triangulate({"vertices": local - origin, "segments": segments}, f"pY{quality}QS{local_budget}")
        if "vertices" in result:
            result["vertices"] += origin
        if len(result.get("vertices", [])) > len(ring):
            points = shapely.points(result["vertices"])
            first, second = shapely.STRtree(points).query(points, predicate="dwithin", distance=tolerance)
            if (first != second).any():
                # Avoid a Steiner insertion that would collapse during welding.
                result = triangle.triangulate({"vertices": local - origin, "segments": segments}, "pYQS0")
                result["vertices"] += origin
        if "triangles" not in result:
            raise MeshError("Could not conform an input element to neighboring nodes.")
        # Input edges stay fixed, preventing propagation into neighboring cells.
        # Interior Steiner nodes interpolate Z in the original parent, without
        # changing any existing source value or crossing attribute boundaries.
        mapping = list(ring)
        parent = np.asarray([index.nodes[j] for j in cell.nodes])
        for point in result["vertices"][len(ring):]:
            z = None
            for k in range(1, len(parent) - 1):
                tri_xyz = parent[[0, k, k + 1]]
                matrix = (tri_xyz[1:, :2] - tri_xyz[0, :2]).T
                if abs(np.linalg.det(matrix)) <= tolerance**2:
                    continue
                weights = np.linalg.solve(matrix, point - tri_xyz[0, :2])
                if weights.min() >= -1e-7 and weights.sum() <= 1 + 1e-7:
                    z = tri_xyz[0, 2] + np.dot(weights, tri_xyz[1:, 2] - tri_xyz[0, 2])
                    break
            if z is None:
                raise MeshError("Could not interpolate a repaired source node inside its parent element.")
            mapping.append(index.add(*point, float(z)))
        for n, tri in enumerate(result["triangles"]):
            eid = cell.id if n == 0 else next_id
            if n:
                next_id += 1
            output.append(replace(cell, nodes=tuple(mapping[int(j)] for j in tri), id=eid))
        if len(output) > max_elements:
            raise MeshError("Source connectivity repair exceeds the element budget.")
    announce(f"Source repair returned {len(output):,} elements")
    return output, len(affected)
