"""Repair only affected source cells; query all original edges in one batch."""
from dataclasses import replace
import numpy as np
import shapely
import triangle

from .model import MeshError


def conform_sources(index, cells, source_count, tolerance):
    if not cells:
        return [], 0
    edge_arrays, owner_arrays = [], []
    for size in (3, 4):
        selection = np.asarray([i for i, c in enumerate(cells) if len(c.nodes) == size], dtype=int)
        if not len(selection):
            continue
        ids = np.asarray([cells[i].nodes for i in selection])
        edge_arrays.append(np.sort(np.stack([ids, np.roll(ids, -1, axis=1)], axis=2).reshape(-1, 2), axis=1))
        owner_arrays.append(np.repeat(selection, size))
    unique, inverse = np.unique(np.concatenate(edge_arrays), axis=0, return_inverse=True)
    owners = np.concatenate(owner_arrays)
    xyz = np.asarray(index.nodes)
    point_tree = shapely.STRtree(shapely.points(xyz[:, :2]))
    edges = shapely.linestrings(xyz[unique, :2])
    edge_hit, node_hit = point_tree.query(edges, predicate="dwithin", distance=tolerance * .25)
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
    for i, cell in enumerate(cells):
        if len(cell.nodes) == 4:
            xy = xyz[list(cell.nodes), :2]
            delta = np.roll(xy, -1, axis=0) - xy
            turn = delta[:, 0] * np.roll(delta[:, 1], -1) - delta[:, 1] * np.roll(delta[:, 0], -1)
            if not ((turn >= 0).all() or (turn <= 0).all()):
                affected.add(i)
    output = []
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
        result = triangle.triangulate({"vertices": local, "segments": segments}, "pYQ")
        if "triangles" not in result:
            raise MeshError("Could not conform an input element to neighboring nodes.")
        for n, tri in enumerate(result["triangles"]):
            eid = cell.id if n == 0 else next_id
            if n:
                next_id += 1
            output.append(replace(cell, nodes=tuple(ring[int(j)] for j in tri), id=eid))
    return output, len(affected)
