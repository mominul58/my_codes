import csv
from io import StringIO
import json

import numpy as np
import pytest
import shapely
from shapely.geometry import box, Polygon

from paveforge import generate_basepoly, read_basepoly, merge_with_transition, write_2dm, write_medit
from paveforge.basepoly import BasePoly, Region
from paveforge.model import Cell, Mesh, MeshError, NodeIndex, select_cells
from paveforge.quality import interior_angles, quality_report, angle_failures_csv
from paveforge.validation import validate_mesh
from paveforge.repair import repair_mesh_angles
from paveforge.boundaries import align_basepoly


def grid():
    nodes = np.asarray([(x, y, 2.) for y in range(0, 31, 5) for x in range(0, 61, 5)])
    cells = []
    for y in range(6):
        for x in range(12):
            n = y * 13 + x
            cells.append(Cell((n, n + 1, n + 14, n + 13), material=99 if x == 0 else 7,
                              attributes=("0.04", "retained"), id=len(cells) + 1))
    return Mesh(nodes, cells, node_ids=list(range(1, len(nodes) + 1)))


def road():
    return Mesh(np.array([[28., 0., 2.], [32., 0., 2.], [32., 30., 2.], [28., 30., 2.]]),
                [Cell((0, 1, 2, 3), material=3, structure=True, attributes=("0.003", "road"), id=500)],
                node_ids=[1000, 1001, 1002, 1003])


def test_all_interior_angles_include_reflex_angles_and_are_translation_invariant():
    xy = np.array([[0., 0.], [2., 0.], [.5, .5], [0., 2.]])
    expected = interior_angles(xy)
    assert expected.max() > 180
    np.testing.assert_allclose(interior_angles(xy + [500000, 5550000]), expected, atol=1e-8)
    np.testing.assert_allclose(interior_angles(xy[::-1])[::-1], expected)


def test_original_bad_elements_block_both_exports_but_allow_exact_id_diagnostics():
    polygon = Polygon([(0, 0), (10, 0), (10, .1)])
    source = Mesh(np.array([[0., 0., 3.], [10., 0., 3.], [10., .1, 3.]]),
                  [Cell((0, 1, 2), structure=True, material=8, id=900, attributes=("a",))], node_ids=[11, 22, 33])
    result = generate_basepoly(BasePoly([Region(polygon, 1)]), structure=source,
                               enforce_angle_limits=True, repair_source_angles=True, minimum_angle=10)
    assert result.report["export_blocked"]
    assert result.report["quality"]["structure_elements_failing_angles"] > 0
    for writer in (write_2dm, write_medit):
        with pytest.raises(MeshError, match="angle limits"):
            writer(result)
    with pytest.raises(MeshError, match="angle limits"):
        write_2dm(select_cells(result, structure=True))
    rows = list(csv.DictReader(StringIO(angle_failures_csv(result))))
    assert "900" in {row["element_id"] for row in rows}
    np.testing.assert_array_equal(result.nodes[:3], source.nodes)
    json.dumps(result.report)


def test_full_weld_distance_is_used_for_validation():
    nodes = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [1.0006, 0, 0], [2, 0, 0], [2, 1, 0]], dtype=float)
    mesh = Mesh(nodes, [Cell((0, 1, 2)), Cell((3, 4, 5))])
    domain = shapely.union_all([Polygon(nodes[list(c.nodes), :2]) for c in mesh.cells])
    with pytest.raises(MeshError, match="near-duplicate"):
        validate_mesh(mesh, domain, .001)


def test_near_structure_area_and_regional_limits_both_apply():
    base = BasePoly([Region(box(0, 0, 60, 30), 1, max_area=20)])
    mesh = generate_basepoly(base, 40., road(), near_structure_area=10., near_structure_distance=8., minimum_angle=10,
                             repair_source_angles=True, enforce_angle_limits=True)
    assert mesh.report["quality"]["all_elements_checked"] == len(mesh.cells)
    assert mesh.report["quality"]["angles_passed"]
    near, far = [], []
    source = box(28, 0, 32, 30)
    for cell in mesh.cells:
        if cell.structure:
            continue
        p = Polygon(mesh.nodes[list(cell.nodes), :2])
        (near if p.centroid.distance(source) < 8 - 1e-6 else far).append(p.area)
    assert near and far
    assert max(near) <= 10 * (1 + 1e-7)
    assert max(far) <= 20 * (1 + 1e-7)
    assert max(far) > 10
    assert mesh.report["duplicate_nodes"] == mesh.report["hanging_nodes"] == 0


def test_erase_refill_merge_keeps_far_material_99_and_source_attributes():
    base, structure = grid(), road()
    mesh = merge_with_transition(base, structure, selection_distance=8., erase_material=99, fill_area=10., fill_material=5)
    report = mesh.report["transition_merge"]
    assert report["erased_base_elements"] > 0 and report["retained_base_elements"] > 0
    assert report["coordinate_system"] == "EPSG:25832"
    assert not report["coordinates_reprojected"]
    assert mesh.report["gap_area"] == mesh.report["overlap_area"] == 0
    assert mesh.report["duplicate_nodes"] == mesh.report["hanging_nodes"] == 0
    assert mesh.report["quality"]["angles_passed"]
    erased = set(report["erased_base_element_ids"])
    surviving = {c.id: c for c in mesh.cells if c.id is not None}
    for cell in base.cells:
        if cell.id not in erased:
            assert surviving[cell.id].material == cell.material
            assert surviving[cell.id].attributes == cell.attributes
    assert any(c.material == 99 for c in mesh.cells)
    assert all(c.material == 3 and c.attributes == ("0.003", "road") for c in mesh.cells if c.structure)
    new = [c for c in mesh.cells if c.id is None]
    assert new and all(c.material == 5 for c in new)
    assert max(Polygon(mesh.nodes[list(c.nodes), :2]).area for c in new) <= 10 * (1 + 1e-7)
    lookup = dict(zip(mesh.node_ids, mesh.nodes))
    for nid, xyz in zip(structure.node_ids, structure.nodes):
        np.testing.assert_array_equal(lookup[nid], xyz)
    json.dumps(mesh.report)


def test_erase_refill_when_entire_base_is_selected_and_holes_remain_empty():
    polygon = Polygon([(0, 0), (60, 0), (60, 30), (0, 30)], holes=[[(3, 3), (6, 3), (6, 6), (3, 6)]])
    base = generate_basepoly(BasePoly([Region(polygon, 1, max_area=10)]), minimum_angle=10)
    mesh = merge_with_transition(base, road(), selection_distance=100., fill_area=10.)
    assert mesh.report["transition_merge"]["retained_base_elements"] == 0
    assert mesh.report["intentional_holes"] == 1
    assert mesh.report["domain_area"] == pytest.approx(polygon.area)
    assert mesh.report["gap_area"] == 0


@pytest.mark.parametrize("kwargs", [{"selection_distance": -1}, {"fill_area": 0}, {"coordinate_system": "EPSG:4326"}])
def test_transition_rejects_invalid_units_or_parameters(kwargs):
    with pytest.raises(MeshError):
        merge_with_transition(grid(), road(), **kwargs)


def test_same_attribute_patches_repair_thin_source_strip_and_preserve_original_values():
    index = NodeIndex(1e-6)
    for x, y in [(0, 0), (10, 0), (10, .5), (0, .5), (0, -2.5), (10, -2.5), (10, 3), (0, 3)]:
        index.add(x + 500000, y + 5550000, .2 * x + .1 * y)
    original = np.asarray(index.nodes).copy()
    cells = [Cell((0, 1, 2), 1, True, ("strip",), 100), Cell((0, 2, 3), 1, True, ("strip",), 101),
             Cell((4, 5, 1, 0), 2, True, ("bottom",), 200), Cell((3, 2, 6, 7), 3, True, ("top",), 300)]
    before = quality_report(Mesh(original, cells))["elements_failing_angles"]
    repaired, audit = repair_mesh_angles(index, cells, len(original), 1e-6, max_passes=3)
    result = Mesh(np.asarray(index.nodes), repaired)
    assert audit
    assert quality_report(result)["elements_failing_angles"] < before
    np.testing.assert_array_equal(result.nodes[:len(original)], original)
    assert {100, 101, 200, 300} <= {c.id for c in repaired}
    assert all(c.attributes == {1: ("strip",), 2: ("bottom",), 3: ("top",)}[c.material] for c in repaired)
    report = validate_mesh(result, box(500000, 5550000 - 2.5, 500010, 5550003), 1e-6)
    assert report["duplicate_nodes"] == report["hanging_nodes"] == 0
    assert report["gap_area"] == report["overlap_area"] == 0


def test_boundary_alignment_reports_changes_and_never_moves_structure_nodes():
    base = BasePoly([Region(box(0, 0, 10, 10), 1), Region(box(4.0001, 2, 6.0001, 8), 2, mode="exclude")])
    structure = box(4, 2, 6, 8)
    cleaned, audit = align_basepoly(base, structure, .001)
    assert cleaned.regions[1].polygon.equals(structure)
    assert base.regions[1].polygon.bounds[0] == 4.0001
    assert audit["changed_features"] == 1
    assert audit["symmetric_difference_area"] > 0


def test_custom_basepoly_field_selectors_match_previous_tool():
    obj = {"type": "Feature", "geometry": shapely.geometry.mapping(box(0, 0, 10, 10)),
           "properties": {"Mode": "Triangular", "Enabled": 0, "Area": 12}}
    result = read_basepoly(json.dumps(obj), field_mapping={"mesh": "Mode", "use_ela": "Enabled", "max_ela": "Area"})
    assert not result.regions[0].use_ela
    assert result.regions[0].properties["Area"] == 12
    with pytest.raises(MeshError, match="missing"):
        read_basepoly(json.dumps(obj), field_mapping={"mesh": "MissingField"})


def test_cleanup_preserves_original_exclusion_when_snapping_would_collapse_it():
    base = BasePoly([Region(box(.1, .1, .2, .2), 42, mode="exclude")])
    cleaned, report = align_basepoly(base, box(0, 0, 1, 1), 1.)
    assert cleaned.regions[0].polygon.equals_exact(base.regions[0].polygon, 0)
    assert cleaned.regions[0].mode == "exclude"
    assert report["skipped_feature_ids"] == [42]
    assert report["symmetric_difference_area"] == 0
