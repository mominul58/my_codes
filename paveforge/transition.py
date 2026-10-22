"""Erase a band of base-mesh cells and pave it around roads/trenches."""
from dataclasses import replace
from time import perf_counter

import numpy as np
import shapely

from .basepoly import BasePoly, Region
from .merge import merge_meshes
from .mesher import generate_basepoly
from .model import Mesh, MeshError
from .validation import polygon_parts


def _faces(mesh):
    result = np.empty(len(mesh.cells), dtype=object)
    for size in (3, 4):
        selected = np.asarray([i for i, c in enumerate(mesh.cells) if len(c.nodes) == size], dtype=int)
        if len(selected):
            result[selected] = shapely.polygons(mesh.nodes[np.asarray([mesh.cells[i].nodes for i in selected]), :2])
    if not shapely.is_valid(result).all() or (shapely.area(result) <= 0).any():
        raise MeshError("Input contains invalid or zero-area elements.")
    if not shapely.coverage_is_valid(result):
        area = shapely.area(result).sum()
        union = shapely.union_all(result)
        if area - union.area > max(1e-8, union.area * 1e-10):
            raise MeshError("Input mesh has overlapping or duplicate elements.")
    return result


def merge_with_transition(base_mesh: Mesh, structure: Mesh, basepoly=None, *,
                          selection_distance=8., erase_material=99, fill_area=10.,
                          fill_material=1, coordinate_system="EPSG:25832", **options):
    """Keep selected-cell IDs in an audit trail; 99 is an erase marker, never a fill material."""
    from pyproj import CRS
    start = perf_counter()
    if not np.isfinite([selection_distance, fill_area]).all() or selection_distance < 0 or fill_area <= 0:
        raise MeshError("Selection distance must be nonnegative and fill area must be positive.")
    if not isinstance(erase_material, int) or erase_material < 1 or not isinstance(fill_material, int) or fill_material < 1:
        raise MeshError("Erase and fill material IDs must be positive integers.")
    try:
        crs = CRS.from_user_input(coordinate_system)
    except Exception as exc:
        raise MeshError("Cannot read the merge coordinate system.") from exc
    if not crs.is_projected or any(abs(a.unit_conversion_factor - 1) > 1e-9 for a in crs.axis_info[:2]):
        raise MeshError("Merge distance and area require a projected CRS in metres.")
    faces, source_faces = _faces(base_mesh), _faces(structure)
    base_coverage = shapely.coverage_union_all(faces) if shapely.coverage_is_valid(faces) else shapely.union_all(faces)
    source_coverage = shapely.coverage_union_all(source_faces) if shapely.coverage_is_valid(source_faces) else shapely.union_all(source_faces)
    # Whole cells are erased, including overlaps, so no fragments or duplicate
    # elements survive beneath the imported structure. Existing material-99
    # elements elsewhere are kept; 99 is only the selected-cell audit marker.
    if selection_distance:
        inner = source_coverage.buffer(selection_distance, quad_segs=16)
        outer = source_coverage.buffer(selection_distance / np.cos(np.pi / 64), quad_segs=16)
        shapely.prepare(inner)
        shapely.prepare(outer)
        erased = shapely.intersects(inner, faces)
        uncertain = ~erased & shapely.intersects(outer, faces)
        erased[uncertain] = shapely.dwithin(source_coverage, faces[uncertain], selection_distance)
    else:
        shapely.prepare(source_coverage)
        erased = shapely.intersects(source_coverage, faces)
    retained = [replace(c, structure=False) for c, remove in zip(base_mesh.cells, erased) if not remove]
    used = sorted({n for c in retained for n in c.nodes})
    remap = {n: i for i, n in enumerate(used)}
    kept_mesh = Mesh(base_mesh.nodes[used], [replace(c, nodes=tuple(remap[n] for n in c.nodes)) for c in retained],
                     list(base_mesh.warnings), node_ids=[base_mesh.node_ids[n] for n in used] if base_mesh.node_ids else [n + 1 for n in used],
                     node_attributes=[base_mesh.node_attributes[n] for n in used] if base_mesh.node_attributes else None)
    # With no basepoly, retain the complete ORIGINAL base footprint, including
    # holes. Using the post-erasure union would hide the transition gap.
    plan = basepoly or BasePoly([Region(p, i + 1, max_area=fill_area) for i, p in enumerate(polygon_parts(base_coverage))])
    # Every newly filled region uses this screen's fill-area ceiling, while
    # smaller enabled per-feature Max_Ela values remain effective.
    plan = BasePoly([replace(r, max_area=min(r.max_area, fill_area) if r.use_ela and r.max_area is not None else fill_area,
                             use_ela=True) for r in plan.regions], list(plan.warnings))
    options.setdefault("minimum_angle", 10.)
    options.setdefault("repair_source_angles", True)
    options.setdefault("enforce_angle_limits", True)
    options.setdefault("allow_structure_outside", True)
    # Fill material is separate from the erase marker. Existing cell attributes
    # remain owned by their source cells during conforming interface repair.
    if retained:
        result = merge_meshes(kept_mesh, structure, plan, fill_area, **options)
        # Newly generated cells have no source ID. merge_meshes uses material 1
        # for those cells; apply only the explicit fill material to this group.
        result.cells = [replace(c, material=fill_material) if c.id is None else c for c in result.cells]
    else:
        result = generate_basepoly(plan, fill_area, structure, material=fill_material, **options)
    erased_cells = [c for c, remove in zip(base_mesh.cells, erased) if remove]
    result.report["transition_merge"] = {
        "selection_distance_m": selection_distance, "erase_material_marker": erase_material,
        "fill_maximum_area_m2": fill_area, "fill_material": fill_material,
        "coordinate_system": crs.to_string(), "coordinate_system_name": crs.name,
        "coordinates_reprojected": False,
        "selection_rule": "Erase whole base cells at distance <= selection distance from the structure, then refill the uncovered footprint.",
        "erased_base_elements": len(erased_cells), "retained_base_elements": len(retained),
        "erased_base_element_ids": [c.id or i + 1 for i, c in enumerate(base_mesh.cells) if erased[i]],
        "erased_base_materials": [c.material for c in erased_cells],
        "erased_base_attributes": [list(c.attributes) for c in erased_cells],
        "base_footprint_area": base_coverage.area,
        "elapsed_seconds": round(perf_counter() - start, 4),
    }
    result.report["elapsed_seconds"] = round(perf_counter() - start, 4)
    return result
