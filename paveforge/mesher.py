from dataclasses import replace
from math import ceil, sqrt
from time import perf_counter

import numpy as np
import shapely
import triangle
from shapely.geometry import LineString, Polygon
from shapely.geometry.polygon import orient

from .basepoly import BasePoly, Region
from .model import Cell, Mesh, MeshError, NodeIndex, default_weld_tolerance
from .validation import polygon_parts, validate_mesh
from .conform import conform_sources
from .quality import check_angle_limits
from .boundaries import align_basepoly
from .repair import repair_mesh_angles

MAX_ELEMENTS = 100_000


def generate_mesh(domain: Polygon, element_size: float, structure: Mesh | None = None,
                  tolerance: float | None = None, material: int = 1,
                  elevation: float = 0., *, max_area: float | None = None,
                  allow_interface_refinement: bool = False) -> Mesh:
    """Legacy single-polygon entry point; optional strict area refinement."""
    if domain.geom_type != "Polygon" or domain.is_empty or not domain.is_valid or domain.area <= 0:
        raise MeshError("Supply a valid, connected polygon with positive area.")
    if not np.isfinite(element_size) or element_size <= 0:
        raise MeshError("Element size must be positive.")
    area = max_area if max_area is not None else sqrt(3) * element_size**2 / 4
    base = BasePoly([Region(domain, 1, max_area=area)])
    return generate_basepoly(base, area, structure, tolerance, material, elevation,
                             strict_area=max_area is not None,
                             refine_interfaces=allow_interface_refinement,
                             target_element_size=element_size)


def _empty_mesh(warnings):
    return Mesh(np.empty((0, 3)), [], warnings, {
        "excluded_only": True, "nodes": 0, "elements": 0, "paving_triangles": 0,
        "structure_elements": 0, "message": "No Triangular area selected. Exclude/blank features produce no mesh file."
    })


def generate_basepoly(basepoly: BasePoly, default_max_area: float = 25.,
                      structure: Mesh | None = None, tolerance: float | None = None,
                      material: int = 1, elevation: float = 0., *, strict_area: bool = True,
                      refine_interfaces: bool = True, target_element_size: float | None = None,
                      max_elements: int = 2_000_000, allow_structure_outside: bool = False,
                      progress=None, minimum_angle: float = 25.,
                      near_structure_area: float | None = None, near_structure_distance: float = 10.,
                      angle_min: float = 5., angle_max: float = 160.,
                      enforce_angle_limits: bool = False, repair_source_angles: bool = False,
                      fixed_segments: bool = False, boundary_cleanup_distance: float = 0.) -> Mesh:
    """Mesh attribute regions together while retaining original source XYZ and attributes."""
    start = perf_counter()
    announce = progress or (lambda message: None)
    check_angle_limits(angle_min, angle_max)
    if not np.isfinite(boundary_cleanup_distance) or boundary_cleanup_distance < 0:
        raise MeshError("Boundary cleanup distance must be nonnegative metres.")
    if near_structure_area is not None and (not np.isfinite(near_structure_area) or near_structure_area <= 0
                                           or not np.isfinite(near_structure_distance) or near_structure_distance <= 0):
        raise MeshError("Near-structure maximum area and refinement distance must be positive.")
    if not np.isfinite(minimum_angle) or not 0 <= minimum_angle <= 28:
        raise MeshError("Minimum-angle target must be between 0 (fast) and 28 degrees.")
    if not isinstance(max_elements, int) or not 100 <= max_elements <= 5_000_000:
        raise MeshError("Element budget must be an integer from 100 to 5,000,000.")
    announce("Preparing basepoly geometry")
    if not np.isfinite(default_max_area) or default_max_area <= 0 or not np.isfinite(elevation):
        raise MeshError("Maximum element area must be positive; elevation must be finite.")
    outer = basepoly.outer
    if outer.is_empty or not any(r.mode == "triangular" for r in basepoly.regions):
        return _empty_mesh(list(basepoly.warnings))
    if not outer.is_valid:
        raise MeshError("Basepoly union is invalid.")
    excluded = basepoly.exclusions
    paving_domain = outer.difference(excluded)
    bounds = outer.bounds
    span = float(np.hypot(bounds[2] - bounds[0], bounds[3] - bounds[1]))
    tolerance = default_weld_tolerance(span) if tolerance is None else tolerance
    if not np.isfinite(tolerance) or not 0 < tolerance < min(sqrt(default_max_area) / 100, span / 1000):
        raise MeshError("Weld tolerance must be positive and much smaller than the area scale and polygon.")
    index = NodeIndex(tolerance)
    preferred_ids, node_attributes = [], []
    aliases = {}
    struct_polys, source_cells = [], []
    warnings = list(basepoly.warnings) + (list(structure.warnings) if structure else [])
    if structure:
        if len(structure.cells) > max_elements or not np.isfinite(structure.nodes).all():
            raise MeshError("Structure is too large or has nonfinite coordinates.")
        source_map = []
        for n, (x, y, z) in enumerate(structure.nodes):
            i = index.add(x, y, z, check_z=True)
            if abs(index.nodes[i][2] - z) > 1e-12:
                raise MeshError("Coincident nodes have conflicting elevations; cannot preserve both values in a shared node.")
            source_map.append(i)
            source_id = structure.node_ids[n] if structure.node_ids else n + 1
            attributes = structure.node_attributes[n] if structure.node_attributes else ()
            if i == len(preferred_ids):
                preferred_ids.append(source_id)
                node_attributes.append(attributes)
            else:
                if attributes != node_attributes[i]:
                    raise MeshError("Coincident nodes have conflicting node attributes; cannot weld without changing values.")
                aliases[str(source_id)] = preferred_ids[i]
        for cell in structure.cells:
            if len(cell.nodes) not in (3, 4):
                raise MeshError("Structures must contain triangles or quadrilaterals.")
            ids = tuple(source_map[i] for i in cell.nodes)
            p = Polygon([index.nodes[i][:2] for i in ids])
            if not p.is_valid or p.area <= 0:
                raise MeshError("Structure contains a self-intersecting or degenerate element.")
            ccw = p.exterior.is_ccw
            struct_polys.append(p)
            source_cells.append(replace(cell, nodes=ids if ccw else ids[::-1]))
        announce("Checking original structured mesh coverage")
        if not shapely.coverage_is_valid(struct_polys):
            tree = shapely.STRtree(struct_polys)
            first, second = tree.query(struct_polys, predicate="intersects")
            mask = first < second
            if mask.any():
                intersections = shapely.intersection(np.asarray(struct_polys, dtype=object)[first[mask]], np.asarray(struct_polys, dtype=object)[second[mask]])
                if (shapely.area(intersections) > tolerance**2).any():
                    raise MeshError("Structure elements overlap or are duplicated.")
    source_node_count = len(index.nodes)
    structure_union = (shapely.coverage_union_all(struct_polys) if shapely.coverage_is_valid(struct_polys) else shapely.union_all(struct_polys)) if struct_polys else Polygon()
    cleanup_report = {"distance_m": boundary_cleanup_distance, "changed_features": 0, "symmetric_difference_area": 0.}
    if boundary_cleanup_distance and struct_polys:
        announce("Aligning basepoly boundaries to fixed source edges")
        original_warning_count = len(basepoly.warnings)
        basepoly, cleanup_report = align_basepoly(basepoly, structure_union, boundary_cleanup_distance)
        warnings.extend(basepoly.warnings[original_warning_count:])
        outer, excluded = basepoly.outer, basepoly.exclusions
        paving_domain = outer.difference(excluded)
    outside_structure_area = structure_union.difference(outer).area if struct_polys else 0.
    if outside_structure_area > max(tolerance**2, outer.area * 1e-12):
        if not allow_structure_outside:
            raise MeshError("Structure must lie entirely inside the basepoly footprint. Enable 'Keep outside structure' to preserve the complete source mesh instead of clipping it.")
        warnings.append(f"Preserved {outside_structure_area:.6f} m² of structure outside basepoly; the merged reference footprint includes it. No paving is generated outside Triangular features.")
    domain = paving_domain.union(structure_union)
    if domain.is_empty:
        return _empty_mesh(warnings)
    zones = []
    announce("Building regional area constraints")
    for region in basepoly.regions:
        if region.mode != "triangular":
            continue
        limit = region.max_area if region.use_ela and region.max_area is not None else default_max_area
        if not np.isfinite(limit) or limit <= 0:
            raise MeshError("Max_Ela must be positive m² for every enabled feature.")
        clipped = region.polygon.difference(excluded).difference(structure_union)
        for part in polygon_parts(clipped):
            zones.append((part, limit, region))
    planned = sum(p.area / area for p, area, _ in zones) + len(source_cells)
    if planned > max_elements:
        raise MeshError(f"Requested size exceeds the {max_elements:,}-element planning budget. Increase Max_Ela or the element budget.")
    # Regional sizing is handled in one Triangle call rather than independent,
    # potentially disconnected meshes for each polygon.
    lines = []
    limits = [area for _, area, _ in zones]
    boundary_size = target_element_size or sqrt(4 * min(limits, default=default_max_area) / sqrt(3))
    if domain.length / boundary_size > max_elements:
        raise MeshError("Too many boundary segments. Increase Max_Ela.")
    for p in polygon_parts(domain):
        for ring in (p.exterior, *p.interiors):
            xy = list(ring.coords)
            for a, b in zip(xy, xy[1:]):
                count = max(1, ceil(np.linalg.norm(np.subtract(b, a)) / boundary_size))
                lines.append(LineString(np.linspace(a, b, count + 1)))
    # Internal structure edges do not participate in paving: use just its union
    # boundary and leave all interior source cells intact.
    lines.extend(p.boundary for p in polygon_parts(structure_union))
    lines.extend(p.boundary for p, _, _ in zones)
    announce("Noding paving and interface boundaries")
    noded = shapely.union_all(lines)
    segments = set()
    for line in noded.geoms if hasattr(noded, "geoms") else [noded]:
        xy = list(line.coords)
        for a, b in zip(xy, xy[1:]):
            u, v = index.add(*a, elevation), index.add(*b, elevation)
            if u != v:
                segments.add(tuple(sorted((u, v))))
    # Welding can move a new intersection onto an existing source node. Node
    # the resulting geometry again before Triangle sees it; otherwise tiny
    # crossing loops may be created without a regional seed.
    for _ in range(6):
        edge_array = np.asarray(sorted(segments))
        graph_lines = shapely.linestrings(np.asarray(index.nodes)[edge_array, :2])
        graph = shapely.union_all(graph_lines)
        updated = set()
        for line in graph.geoms if hasattr(graph, "geoms") else [graph]:
            xy = list(line.coords)
            for a, b in zip(xy, xy[1:]):
                u, v = index.add(*a, elevation), index.add(*b, elevation)
                if u != v:
                    updated.add(tuple(sorted((u, v))))
        if updated == segments:
            break
        segments = updated
    else:
        raise MeshError("Boundary welding did not converge. Use a smaller node merge distance.")
    if len(segments) > max_elements:
        raise MeshError("Too many constrained segments. Simplify basepoly or increase Max_Ela.")
    retained = []
    generated = []
    if zones:
        # Seed each actual graph face after welding. Regional polygon fragments
        # can split or vanish at the weld scale; seeding their old geometries
        # left unassigned slivers in previous builds.
        faces = list(shapely.polygonize(shapely.linestrings(np.asarray(index.nodes)[np.asarray(sorted(segments)), :2])).geoms)
        points = shapely.point_on_surface(faces)
        face_ids, zone_ids = shapely.STRtree([p for p, _, _ in zones]).query(points, predicate="within")
        assignments = dict(zip(map(int, face_ids), map(int, zone_ids)))
        holes, regions = [], []
        for i, point in enumerate(points):
            if i in assignments:
                z = assignments[i]
                regions.append([point.x, point.y, z + 1, zones[z][1]])
            else:
                holes.append([point.x, point.y])
        if not regions:
            raise MeshError("No paving region survives the node merge distance. Reduce the merge distance.")
        active = np.unique(np.asarray(sorted(segments)))
        compact = np.full(len(index.nodes), -1, dtype=int)
        compact[active] = np.arange(len(active))
        data = {"vertices": np.asarray(index.nodes)[active, :2], "segments": compact[np.asarray(sorted(segments))], "regions": np.asarray(regions)}
        if holes:
            data["holes"] = np.asarray(holes)
        quality = f"q{minimum_angle:g}" if minimum_angle else ""
        flags = f"p{quality}aAS{max_elements}Q" + ("YY" if fixed_segments else ("" if refine_interfaces else "Y"))
        announce(f"Triangulating {len(zones):,} regions; estimated minimum {planned:,.0f} total elements")
        result = triangle.triangulate(data, flags)
        if "triangles" not in result:
            raise MeshError("Triangulation failed; check basepoly and structure boundaries.")
        near_triangles = np.zeros(len(result["triangles"]), dtype=bool)
        refinement_passes = 0
        if near_structure_area is not None and struct_polys:
            # Refine by distance using triangle-area constraints instead of
            # slicing the PSLG with a buffer. Buffer intersections created many
            # tiny constrained wedges along roads. Each child inherits its
            # parent's area ceiling, so refinement is conservative at the band.
            # Prepared polygon intersection is much faster than a distance
            # calculation per triangle on a large road network. Inflate the
            # inscribed circular buffer slightly so the complete requested
            # distance is covered; extra refinement outside it is harmless.
            sizing_band = structure_union.buffer(near_structure_distance / np.cos(np.pi / 64), quad_segs=16)
            shapely.prepare(sizing_band)
            announce("Refining paving areas near the structure")
            for _ in range(8):
                coords = result["vertices"][result["triangles"]]
                near_triangles = shapely.intersects(sizing_band, shapely.polygons(coords))
                attributes = np.rint(result["triangle_attributes"].ravel()).astype(int) - 1
                if (attributes < 0).any() or (attributes >= len(zones)).any():
                    raise MeshError("A paving triangle lost its basepoly region assignment.")
                local_limits = np.asarray([area for _, area, _ in zones])[attributes]
                local_limits[near_triangles] = np.minimum(local_limits[near_triangles], near_structure_area)
                areas = shapely.area(shapely.polygons(coords))
                if not (areas > local_limits * (1 + 1e-7)).any():
                    break
                refine_data = {k: result[k] for k in ("vertices", "triangles", "segments", "triangle_attributes", "vertex_markers", "segment_markers") if k in result}
                refine_data["triangle_max_area"] = local_limits[:, None]
                result = triangle.triangulate(refine_data, f"rp{quality}aS{max_elements}Q" + ("YY" if fixed_segments else ("" if refine_interfaces else "Y")))
                refinement_passes += 1
            else:
                raise MeshError("Near-structure area refinement did not converge within eight passes. Increase the area or element budget.")
        mapping = [index.add(x, y, elevation) for x, y in result["vertices"]]
        zone_indices = np.rint(result["triangle_attributes"].ravel()).astype(int) - 1
        if (zone_indices < 0).any() or (zone_indices >= len(zones)).any():
            raise MeshError("A paving triangle lost its basepoly region assignment.")
        connectivity = np.asarray(mapping)[result["triangles"]]
        xy = np.asarray(index.nodes)[connectivity, :2]
        a, b = xy[:, 1] - xy[:, 0], xy[:, 2] - xy[:, 0]
        actual_areas = np.abs(a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]) / 2
        collapsed = actual_areas <= 0
        if collapsed.any():
            raise MeshError(
                f"Node welding collapsed {int(collapsed.sum()):,} new paving elements at the "
                f"{tolerance:g} m merge distance. No mesh can be exported. Reduce the node "
                "merge distance, or regenerate an old base mesh from basepoly before merging. "
                "Deleting these cells could leave voids or disconnected interfaces."
            )
        area_limits = np.asarray([limit for _, limit, _ in zones])[zone_indices]
        if near_structure_area is not None and struct_polys:
            area_limits[near_triangles] = np.minimum(area_limits[near_triangles], near_structure_area)
        if strict_area and (actual_areas > area_limits * (1 + 1e-7)).any():
            raise MeshError("Max_Ela could not be met within the refinement budget. Increase Max_Ela or simplify the geometry.")
        generated = [Cell(tuple(map(int, ids)), material, region=zones[int(z)][2].feature) for ids, z in zip(connectivity, zone_indices)]
    announce("Connecting new interface nodes to original structure elements")
    retained, repaired = conform_sources(index, source_cells, source_node_count, tolerance,
                                         minimum_angle=min(28., max(minimum_angle, angle_min, (180 - angle_max) / 2)) if repair_source_angles else 0.,
                                         angle_min=angle_min, angle_max=angle_max, max_elements=max_elements, progress=announce)
    if repaired:
        warnings.append(f"Triangulated {repaired} input cells to conform interfaces; child cells retain all parent element attributes.")
    cells = retained + generated
    angle_repairs = []
    if repair_source_angles:
        cells, angle_repairs = repair_mesh_angles(index, cells, source_node_count, tolerance,
                                                  minimum=angle_min, maximum=angle_max, target=minimum_angle,
                                                  max_elements=max_elements, progress=announce,
                                                  maximum_paving_area=min([*limits, default_max_area] + ([near_structure_area] if near_structure_area is not None else [])),
                                                  fixed_segments=fixed_segments)
    if len(cells) > max_elements:
        raise MeshError(f"Generated mesh exceeds {max_elements:,} elements. Increase Max_Ela or the element budget.")
    used = sorted({i for c in cells for i in c.nodes})
    remap = {old: new for new, old in enumerate(used)}
    next_node = max(structure.node_ids if structure and structure.node_ids else preferred_ids, default=0) + 1
    ids, extras = [], []
    for old in used:
        if old < source_node_count:
            ids.append(preferred_ids[old])
            extras.append(node_attributes[old])
        else:
            ids.append(next_node)
            next_node += 1
            extras.append(())
    mesh = Mesh(np.asarray([index.nodes[i] for i in used]),
                [replace(c, nodes=tuple(remap[i] for i in c.nodes)) for c in cells], warnings,
                node_ids=ids, node_attributes=extras)
    new_paving = [c for c in mesh.cells if not c.structure and c.id is None]
    max_generated_area = 0.
    if new_paving:
        new_polys = shapely.polygons(mesh.nodes[np.asarray([c.nodes for c in new_paving]), :2])
        max_generated_area = float(shapely.area(new_polys).max())
    if zones and near_structure_area is not None and struct_polys:
        paving = np.asarray([c.nodes for c in new_paving if len(c.nodes) == 3])
        if len(paving):
            near_polys = shapely.polygons(mesh.nodes[paving, :2])
            near_triangles = shapely.intersects(sizing_band, near_polys)
            near_areas = shapely.area(near_polys)[near_triangles]
        else:
            near_triangles, near_areas = np.array([], dtype=bool), np.array([])
    else:
        near_triangles, near_areas = np.array([], dtype=bool), np.array([])
    announce(f"Validating {len(cells):,} elements")
    mesh.report = validate_mesh(mesh, domain, tolerance, angle_min=angle_min, angle_max=angle_max)
    mesh.report.update({"target_element_size": target_element_size, "default_max_area": default_max_area,
                        "target_area": max(limits, default=default_max_area), "strict_max_area": strict_area,
                        "new_paving_triangles": len(new_paving), "max_generated_paving_area": max_generated_area,
                        "weld_tolerance": tolerance, "input_domain_area": domain.area,
                        "welded_node_id_map": aliases,
                        "outside_structure_area": outside_structure_area, "element_budget": max_elements,
                        "angle_target": minimum_angle,
                        "triangle_options": flags if zones else None,
                        "near_structure_max_area": near_structure_area,
                        "near_structure_distance": near_structure_distance if near_structure_area is not None else None,
                        "near_structure_paving_triangles": int(np.count_nonzero(near_triangles)) if zones else 0,
                        "max_refinement_band_paving_area": float(near_areas.max()) if len(near_areas) else None,
                        "near_structure_refinement_passes": refinement_passes if zones else 0,
                        "angle_limits_enforced": enforce_angle_limits,
                        "export_blocked": enforce_angle_limits and not mesh.report["quality"]["angles_passed"],
                        "source_angle_repair_enabled": repair_source_angles,
                        "angle_connectivity_repairs": angle_repairs,
                        "fixed_constraint_segments": fixed_segments,
                        "boundary_cleanup": cleanup_report,
                        "basepoly_features": [{"feature": r.feature, "Mesh": r.mode, "use_Ela": int(r.use_ela),
                                               "Max_Ela": r.max_area, "properties": r.properties} for r in basepoly.regions],
                        "elapsed_seconds": round(perf_counter() - start, 4)})
    if not strict_area and mesh.report["max_paving_area"] > max(limits, default=default_max_area) * 1.05:
        warnings.append("Some paving elements exceed the target size because interface edges are fixed.")
    if not mesh.report["quality"]["angles_passed"]:
        q = mesh.report["quality"]
        warnings.append(f"{q['elements_failing_angles']:,} elements fail the {angle_min:g}°–{angle_max:g}° angle limits "
                        f"({q['structure_elements_failing_angles']:,} structure, {q['paving_elements_failing_angles']:,} paving). "
                        "Fixed acute corners and attribute boundaries can prevent repair without moving original nodes. Inspect the angle-failure CSV.")
    return mesh
