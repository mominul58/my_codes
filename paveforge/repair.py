"""Bounded angle repair across adjacent cells with identical attributes."""
from collections import Counter
from dataclasses import replace

import numpy as np
import shapely
import triangle

from .conform import conform_sources
from .model import Mesh, MeshError
from .quality import cell_angle_extrema, interior_angles
from .validation import polygon_parts


def _signature(cell):
    return cell.material, cell.attributes, cell.structure, cell.region


def _bad(index, cells, minimum, maximum):
    low, high = cell_angle_extrema(Mesh(np.asarray(index.nodes), cells))
    return (low < minimum - 1e-7) | (high > maximum + 1e-7)


def _rollback(index, length):
    from math import floor
    while len(index.nodes) > length:
        i = len(index.nodes) - 1
        x, y, _ = index.nodes.pop()
        key = floor(x / index.tolerance), floor(y / index.tolerance)
        index.buckets[key].remove(i)
        if not index.buckets[key]:
            del index.buckets[key]


def _z_at(point, index, parents):
    for cell in parents:
        xyz = np.asarray([index.nodes[n] for n in cell.nodes])
        for k in range(1, len(xyz) - 1):
            tri = xyz[[0, k, k + 1]]
            matrix = (tri[1:, :2] - tri[0, :2]).T
            if abs(np.linalg.det(matrix)) < 1e-18:
                continue
            weights = np.linalg.solve(matrix, point - tri[0, :2])
            if weights.min() >= -1e-7 and weights.sum() <= 1 + 1e-7:
                return float(tri[0, 2] + weights @ (tri[1:, 2] - tri[0, 2]))
    return None


def repair_mesh_angles(index, cells, source_count, tolerance, *, minimum=5., maximum=160.,
                       target=10., max_elements=2_000_000, max_passes=2, progress=None,
                       maximum_paving_area=None, fixed_segments=False):
    announce = progress or (lambda message: None)
    audit = []
    for attempt in range(max_passes):
        bad = _bad(index, cells, minimum, maximum)
        before = int(bad.sum())
        if not before:
            break
        announce(f"Angle repair pass {attempt + 1}: {before:,} failing elements")
        edge_parts, owner_parts = [], []
        for size in (3, 4):
            selected = np.asarray([i for i, c in enumerate(cells) if len(c.nodes) == size], dtype=int)
            if len(selected):
                ids = np.asarray([cells[i].nodes for i in selected])
                edge_parts.append(np.sort(np.stack((ids, np.roll(ids, -1, axis=1)), axis=2).reshape(-1, 2), axis=1))
                owner_parts.append(np.repeat(selected, size))
        edges, owners = np.concatenate(edge_parts), np.concatenate(owner_parts)
        _, inverse = np.unique(edges, axis=0, return_inverse=True)
        wanted = np.unique(inverse[bad[owners]])
        selected_edges = np.flatnonzero(np.isin(inverse, wanted))
        by_edge = {}
        for i in selected_edges:
            by_edge.setdefault(int(inverse[i]), []).append(int(owners[i]))
        touching = {}
        for neighbors in by_edge.values():
            for i in neighbors:
                if bad[i]:
                    touching.setdefault(i, set()).update(neighbors)
        groups = []
        for i in np.flatnonzero(bad):
            group = {int(i)} | {j for j in touching.get(int(i), ()) if _signature(cells[j]) == _signature(cells[i])}
            merged = [g for g in groups if g & group]
            for g in merged:
                groups.remove(g)
                group |= g
            groups.append(group)
        del edges, owners, inverse, edge_parts, owner_parts, selected_edges, by_edge
        length = len(index.nodes)
        original_nodes = list(index.nodes)
        removed, replacements, patches = set(), [], []
        next_id = max((c.id or 0 for c in cells), default=0) + 1
        for group in groups:
            if len(group) > 64:
                continue
            parents = [cells[i] for i in sorted(group)]
            used = sorted({n for c in parents for n in c.nodes})
            if len(used) > 256:
                continue
            mapping = {n: i for i, n in enumerate(used)}
            boundary = Counter(tuple(sorted(e)) for c in parents for e in zip(c.nodes, c.nodes[1:] + c.nodes[:1]))
            segments = np.asarray([[mapping[a], mapping[b]] for (a, b), count in boundary.items() if count == 1])
            xy = np.asarray([index.nodes[n][:2] for n in used])
            domain = shapely.union_all([shapely.Polygon([index.nodes[n][:2] for n in c.nodes]) for c in parents])
            data = {"vertices": xy - xy[0], "segments": segments}
            holes = [shapely.Polygon(r).representative_point().coords[0] for p in polygon_parts(domain) for r in p.interiors]
            if holes:
                data["holes"] = np.asarray(holes) - xy[0]
            area_flag = f"a{maximum_paving_area:.17g}" if maximum_paving_area is not None and not parents[0].structure else ""
            result = triangle.triangulate(data, f"p{'YY' if fixed_segments else ''}q{min(28., max(10., target)):g}{area_flag}QS32")
            if "triangles" not in result:
                continue
            angles = interior_angles(result["vertices"][result["triangles"]])
            remaining = int(((angles.min(axis=1) < minimum - 1e-7) | (angles.max(axis=1) > maximum + 1e-7)).sum())
            old_ids = [c.id for c in parents if c.id is not None]
            if remaining >= int(bad[list(group)].sum()) or len(result["triangles"]) < len(old_ids):
                continue
            local_length = len(index.nodes)
            ids = list(used)
            for point in result["vertices"][len(used):] + xy[0]:
                z = _z_at(point, index, parents)
                if z is None:
                    break
                ids.append(index.add(*point, z))
            if len(ids) != len(result["vertices"]):
                _rollback(index, local_length)
                continue
            connectivity = np.asarray(ids)[result["triangles"]]
            # A patch contains only a few cells. Converting the whole million-
            # node mesh for each patch made repair unnecessarily expensive.
            coords = np.asarray([[index.nodes[int(n)][:2] for n in conn] for conn in connectivity])
            polys = shapely.polygons(coords)
            if not shapely.is_valid(polys).all() or (shapely.area(polys) <= tolerance**2).any():
                _rollback(index, local_length)
                continue
            if area_flag and (shapely.area(polys) > maximum_paving_area * (1 + 1e-7)).any():
                _rollback(index, local_length)
                continue
            # A patch cannot change its footprint or transfer attributes across
            # different material/region boundaries.
            if domain.symmetric_difference(shapely.union_all(polys)).area > max(1e-8, domain.area * 1e-9):
                _rollback(index, local_length)
                continue
            children = []
            for j, conn in enumerate(connectivity):
                eid = old_ids[j] if j < len(old_ids) else (next_id if old_ids else None)
                if j >= len(old_ids) and old_ids:
                    next_id += 1
                children.append(replace(parents[0], nodes=tuple(map(int, conn)), id=eid))
            removed |= group
            replacements.extend(children)
            patches.append({"parent_ids": old_ids, "replacement_ids": [c.id for c in children], "material": parents[0].material})
        if not patches:
            break
        candidate = [c for i, c in enumerate(cells) if i not in removed] + replacements
        try:
            candidate, _ = conform_sources(index, candidate, source_count, tolerance, max_elements=max_elements)
        except MeshError as exc:
            _rollback(index, length)
            index.nodes[:] = original_nodes
            audit.append({"pass": attempt + 1, "accepted": False, "before": before, "after": before,
                          "reason": str(exc)})
            announce("Kept the previous connectivity because this angle-repair pass could not preserve valid interfaces")
            break
        after = int(_bad(index, candidate, minimum, maximum).sum())
        if after >= before or len(candidate) > max_elements:
            _rollback(index, length)
            index.nodes[:] = original_nodes
            break
        cells = candidate
        audit.append({"pass": attempt + 1, "accepted": True, "before": before, "after": after, "patches": patches})
        announce(f"Angle repair reduced failing elements from {before:,} to {after:,}")
    return cells, audit
