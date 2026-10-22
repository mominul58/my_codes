"""Fast merge of existing meshes, with a conforming repair fallback."""
from dataclasses import replace
from time import perf_counter

import numpy as np
from shapely import polygons, union_all, coverage_is_valid, coverage_union_all

from .basepoly import BasePoly, Region
from .mesher import generate_basepoly
from .model import Mesh, MeshError, NodeIndex, default_weld_tolerance
from .validation import polygon_parts, validate_mesh


def merge_meshes(paving: Mesh, structure: Mesh, basepoly: BasePoly | None = None,
                 default_max_area: float = 25., tolerance: float | None = None, *,
                 max_elements: int = 2_000_000, allow_structure_outside: bool = False,
                 minimum_angle: float = 10., angle_min: float = 5., angle_max: float = 160.,
                 enforce_angle_limits: bool = False, repair_source_angles: bool = False,
                 near_structure_area: float | None = None, near_structure_distance: float = 10.,
                 fixed_segments: bool = False, boundary_cleanup_distance: float = 0.) -> Mesh:
    start = perf_counter()
    xy = np.concatenate([paving.nodes, structure.nodes])[:, :2]
    span = float(np.linalg.norm(np.ptp(xy, axis=0)))
    tolerance = default_weld_tolerance(span) if tolerance is None else tolerance
    if not np.isfinite(tolerance) or tolerance <= 0:
        raise MeshError("Weld tolerance must be positive.")
    index = NodeIndex(tolerance)
    ids, attributes, cells = [], [], []
    sources = [("structure", structure, True), ("paving", paving, False)]
    max_node = max((i for _, m, _ in sources for i in (m.node_ids or list(range(1, len(m.nodes) + 1)))), default=0)
    max_element = max((c.id or n + 1 for _, m, _ in sources for n, c in enumerate(m.cells)), default=0)
    next_node, next_element = max_node + 1, max_element + 1
    used_node_ids, used_element_ids = set(), set()
    node_maps, element_maps = {}, {}
    warnings = list(paving.warnings) + list(structure.warnings)
    for name, mesh, is_structure in sources:
        mapping = []
        node_maps[name], element_maps[name] = {}, {}
        for n, xyz in enumerate(mesh.nodes):
            i = index.add(*xyz, check_z=True)
            # Values that differ cannot be preserved in a single shared node.
            if abs(index.nodes[i][2] - xyz[2]) > 1e-12:
                raise MeshError("Coincident nodes have conflicting elevations. A shared node cannot keep both values; align them before merging.")
            source_id = mesh.node_ids[n] if mesh.node_ids else n + 1
            extra = mesh.node_attributes[n] if mesh.node_attributes else ()
            if i == len(ids):
                output_id = source_id
                if output_id in used_node_ids:
                    output_id = next_node
                    next_node += 1
                ids.append(output_id)
                attributes.append(extra)
                used_node_ids.add(output_id)
            elif attributes[i] != extra:
                raise MeshError("Coincident nodes have conflicting node attributes; cannot preserve both in one node.")
            mapping.append(i)
            node_maps[name][str(source_id)] = ids[i]
        for n, cell in enumerate(mesh.cells):
            source_id = cell.id or n + 1
            eid = source_id
            if eid in used_element_ids:
                eid = next_element
                next_element += 1
            used_element_ids.add(eid)
            element_maps[name][str(source_id)] = eid
            cells.append(replace(cell, nodes=tuple(mapping[i] for i in cell.nodes), structure=is_structure, id=eid))
    combined = Mesh(np.asarray(index.nodes), cells, warnings, node_ids=ids, node_attributes=attributes)
    faces = np.empty(len(cells), dtype=object)
    for size in (3, 4):
        selection = np.asarray([i for i, c in enumerate(cells) if len(c.nodes) == size], dtype=int)
        if len(selection):
            connectivity = np.asarray([cells[i].nodes for i in selection])
            faces[selection] = polygons(combined.nodes[connectivity, :2])
    coverage = coverage_union_all(faces) if coverage_is_valid(faces) else union_all(faces)
    if basepoly:
        structure_faces = faces[np.array([c.structure for c in cells])]
        structure_union = coverage_union_all(structure_faces) if coverage_is_valid(structure_faces) else union_all(structure_faces)
        expected = basepoly.domain.union(structure_union)
        if not basepoly.outer.covers(coverage):
            if not allow_structure_outside:
                raise MeshError("An input mesh lies outside the basepoly outer boundary.")
            warnings.append(f"Preserved {coverage.difference(basepoly.outer).area:.6f} m² of existing mesh outside basepoly; the merged reference domain includes it.")
            expected = expected.union(coverage)
    else:
        expected = coverage
        warnings.append("No basepoly supplied: coverage is checked against the input cells only. Existing empty areas cannot be identified as intended exclusions or accidental gaps.")
    fast = True
    try:
        report = validate_mesh(combined, expected, tolerance, angle_min=angle_min, angle_max=angle_max)
        if repair_source_angles and not report["quality"]["angles_passed"]:
            raise MeshError("Angle repair requested.")
    except MeshError as exc:
        if not any(word in str(exc).lower() for word in ("hanging", "coverage", "disconnected", "angle repair")):
            raise
        fast = False
        plan = basepoly or BasePoly([Region(p, i + 1) for i, p in enumerate(polygon_parts(expected))])
        combined = generate_basepoly(plan, default_max_area, combined, tolerance,
                                     max_elements=max_elements, allow_structure_outside=allow_structure_outside,
                                     minimum_angle=minimum_angle, angle_min=angle_min, angle_max=angle_max,
                                     enforce_angle_limits=enforce_angle_limits, repair_source_angles=repair_source_angles,
                                     near_structure_area=near_structure_area, near_structure_distance=near_structure_distance,
                                     fixed_segments=fixed_segments, boundary_cleanup_distance=boundary_cleanup_distance)
        report = combined.report
    report.update({"merge_fast_path": fast, "node_id_map": node_maps, "element_id_map": element_maps,
                   "weld_tolerance": tolerance, "angle_limits_enforced": enforce_angle_limits,
                   "export_blocked": enforce_angle_limits and not report["quality"]["angles_passed"],
                   "coverage_reference": "basepoly" if basepoly else "input cell union",
                   "elapsed_seconds": round(perf_counter() - start, 4)})
    combined.report = report
    if fast and not report["quality"]["angles_passed"]:
        combined.warnings.append("Imported elements fail the requested angle limits; inspect the all-element quality report.")
    return combined
