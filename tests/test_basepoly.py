from io import BytesIO
import json
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pytest
from shapely.geometry import box, mapping, Polygon

from paveforge import generate_basepoly, merge_meshes, read_2dm, read_basepoly, write_2dm
from paveforge.formats import write_attributes
from paveforge.model import MeshError


def plan(*specs):
    return read_basepoly(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": mapping(poly), "properties": props} for poly, props in specs
    ]}))


def quad(x0, y0, x1, y1, *, z=0, eid=10, nid=100, attrs="7 0.0025 12"):
    return read_2dm(f"MESH2D\nE4Q {eid} {nid} {nid+1} {nid+2} {nid+3} {attrs}\n"
                    f"ND {nid} {x0} {y0} {z}\nND {nid+1} {x1} {y0} {z}\n"
                    f"ND {nid+2} {x1} {y1} {z}\nND {nid+3} {x0} {y1} {z}\n")


def test_regional_max_area_and_exclusion():
    base = plan((box(0, 0, 10, 10), {"Mesh": "Triangular", "use_Ela": 1, "Max_Ela": 1}),
                (box(10, 0, 20, 10), {"Mesh": "Triangular", "use_Ela": 1., "Max_Ela": 5}),
                (box(3, 3, 5, 5), {"Mesh": "Exclude", "Max_Ela": 0}),
                (box(13, 3, 15, 5), {"Mesh": "", "Max_Ela": 0}))
    mesh = generate_basepoly(base, 25)
    assert mesh.report["connected_components"] == 1
    assert mesh.report["domain_area"] == 192
    assert mesh.report["gap_area"] == mesh.report["overlap_area"] == 0
    limits = {1: 1, 2: 5}
    for cell in mesh.cells:
        polygon = Polygon(mesh.nodes[list(cell.nodes), :2])
        assert polygon.area <= limits[cell.region] * (1 + 1e-7)
        assert not polygon.intersects(box(3.01, 3.01, 4.99, 4.99))
        assert not polygon.intersects(box(13.01, 3.01, 14.99, 4.99))


@pytest.mark.parametrize("enabled", [1, 1., "1", "1.0", True, None, ""])
def test_use_ela_enabled_forms(enabled):
    base = plan((box(0, 0, 10, 10), {"mesh": "TRIANGULAR", "USE_ELA": enabled, "max_ela": 2}))
    mesh = generate_basepoly(base, 20)
    assert mesh.report["max_paving_area"] <= 2 * (1 + 1e-7)


def test_disabled_use_ela_uses_fallback():
    base = plan((box(0, 0, 10, 10), {"Mesh": "Triangular", "use_Ela": 0, "Max_Ela": "ignored"}))
    mesh = generate_basepoly(base, 10)
    assert 2 < mesh.report["max_paving_area"] <= 10 * (1 + 1e-7)


def test_exclude_only_generates_no_file(tmp_path):
    import subprocess
    import sys

    obj = {"type": "Feature", "geometry": mapping(box(0, 0, 10, 10)), "properties": {"Mesh": "Exclude"}}
    source = tmp_path / "basepoly.geojson"
    source.write_text(json.dumps(obj))
    mesh = generate_basepoly(read_basepoly(source.read_text()))
    assert not mesh.cells and mesh.report["excluded_only"]
    with pytest.raises(MeshError, match="No triangular"):
        write_2dm(mesh)
    result = subprocess.run([sys.executable, "-m", "paveforge.cli", str(source), "--output", str(tmp_path / "unstruct")], capture_output=True, text=True)
    assert result.returncode == 0
    assert not list(tmp_path.glob("unstruct.*"))


def test_empty_excluded_overlay():
    mesh = generate_basepoly(plan((box(0, 0, 10, 10), {"Mesh": "Triangular"}), (box(0, 0, 10, 10), {"Mesh": "Exclude"})))
    assert not mesh.cells


def test_structure_in_excluded_footprint():
    base = plan((box(0, 0, 20, 20), {"Mesh": "Triangular", "Max_Ela": 2}), (box(5, 5, 15, 15), {"Mesh": "Exclude"}))
    mesh = generate_basepoly(base, structure=quad(5, 5, 15, 15, z=3))
    assert mesh.report["domain_area"] == 400
    assert mesh.report["connected_components"] == 1
    assert mesh.report["gap_area"] == 0
    assert mesh.report["max_paving_area"] <= 2 * (1 + 1e-7)


def test_refinement_preserves_original_xyz_and_element_attributes():
    structure = quad(3.123456789123, 3.234567891234, 16.123456789123, 16.234567891234, z=2.123456789123)
    base = plan((box(0, 0, 20, 20), {"Mesh": "Triangular", "Max_Ela": .5}))
    mesh = generate_basepoly(base, structure=structure)
    restored = read_2dm(write_2dm(mesh))
    lookup = dict(zip(restored.node_ids, restored.nodes))
    for source_id, xyz in zip(structure.node_ids, structure.nodes):
        np.testing.assert_array_equal(lookup[source_id], xyz)
    children = [c for c in mesh.cells if c.structure]
    assert len(children) > 1
    assert all(c.material == 7 and c.attributes == ("0.0025", "12") for c in children)
    assert 10 in {c.id for c in children}
    assert mesh.report["max_paving_area"] <= .5 * (1 + 1e-7)
    sidecar = json.loads(write_attributes(mesh))
    assert all(e["attributes"] == ["0.0025", "12"] for e in sidecar["elements"] if e["structure"])


def test_2dm_extended_node_values_and_nonsequential_ids_roundtrip():
    text = "MESH2D\nE3T 42 10 30 80 7 0.024 99\nND 10 1 1 2 0.8\nND 30 2 1 3 0.7\nND 80 1 2 4 0.6\n"
    source = read_2dm(text)
    output = read_2dm(write_2dm(source))
    assert source.node_ids == output.node_ids == [10, 30, 80]
    assert output.cells[0].id == 42
    assert output.cells[0].attributes == ("0.024", "99")
    assert output.node_attributes == [("0.8",), ("0.7",), ("0.6",)]


def test_shapefile_zip_with_fields_and_crs():
    import shapefile
    from pyproj import CRS

    shp, shx, dbf = BytesIO(), BytesIO(), BytesIO()
    with shapefile.Writer(shp=shp, shx=shx, dbf=dbf, shapeType=shapefile.POLYGON) as writer:
        writer.field("Mesh", "C", size=20)
        writer.field("use_Ela", "N", decimal=0)
        writer.field("Max_Ela", "F", decimal=3)
        writer.poly([[(0, 0), (0, 10), (10, 10), (10, 0), (0, 0)]])
        writer.record("Triangular", 1, 2.)
    blob = BytesIO()
    with ZipFile(blob, "w") as archive:
        for ext, data in (("shp", shp), ("shx", shx), ("dbf", dbf)):
            archive.writestr("nested/BasePoly." + ext, data.getvalue())
        archive.writestr("nested/BasePoly.prj", CRS.from_epsg(32646).to_wkt())
    base = read_basepoly(blob.getvalue(), "basepoly.zip")
    assert base.regions[0].max_area == 2
    assert not base.warnings
    assert generate_basepoly(base).report["max_paving_area"] <= 2 * (1 + 1e-7)


@pytest.mark.parametrize("crs", ["EPSG:4326", "EPSG:2263"])
def test_geographic_or_feet_crs_rejected(crs):
    obj = {"type": "Feature", "geometry": mapping(box(0, 0, 10, 10)), "properties": {}, "crs": {"properties": {"name": crs}}}
    with pytest.raises(MeshError, match="metre"):
        read_basepoly(json.dumps(obj))


def test_overlapping_triangular_features_rejected():
    with pytest.raises(MeshError, match="overlap"):
        plan((box(0, 0, 10, 10), {}), (box(5, 5, 15, 15), {}))


@pytest.mark.parametrize("value", [0, -1, "nan", "inf"])
def test_invalid_enabled_max_area_rejected(value):
    with pytest.raises(MeshError, match="positive"):
        plan((box(0, 0, 10, 10), {"Max_Ela": value}))


def test_fast_merge_keeps_values_attributes_and_resolves_id_collisions():
    paving, structure = quad(0, 0, 5, 10, attrs="3 0.1 20"), quad(5, 0, 10, 10, attrs="9 0.3 40")
    mesh = merge_meshes(paving, structure)
    assert mesh.report["merge_fast_path"]
    assert len(mesh.nodes) == 6
    assert len(mesh.cells) == 2
    assert {c.attributes for c in mesh.cells} == {("0.1", "20"), ("0.3", "40")}
    assert mesh.report["node_id_map"]["paving"]["101"] == mesh.report["node_id_map"]["structure"]["100"]
    reread = read_2dm(write_2dm(mesh))
    assert len(set(reread.node_ids)) == len(reread.nodes)
    assert len({c.id for c in reread.cells}) == 2


def test_merge_hanging_node_repairs_children_with_parent_attributes():
    paving = quad(0, 0, 5, 10, attrs="3 0.1 20")
    structure = quad(5, 0, 10, 5, attrs="9 0.3 40")
    mesh = merge_meshes(paving, structure)
    assert not mesh.report["merge_fast_path"]
    assert mesh.report["gap_area"] == 0
    assert mesh.report["hanging_nodes"] == 0
    assert all(c.attributes == (("0.3", "40") if c.structure else ("0.1", "20")) for c in mesh.cells)


def test_merge_conflicting_values_fails():
    with pytest.raises(MeshError, match="conflicting elevations"):
        merge_meshes(quad(0, 0, 5, 10), quad(5, 0, 10, 10, z=3))


def test_merge_with_basepoly_fills_missing_gap():
    mesh = merge_meshes(quad(0, 0, 4, 10), quad(6, 0, 10, 10), plan((box(0, 0, 10, 10), {"Max_Ela": 1})))
    assert mesh.report["gap_area"] == 0
    assert mesh.report["domain_area"] == 100
    assert mesh.report["connected_components"] == 1
    assert not mesh.report["merge_fast_path"]


def test_real_shapefile_field_aliases():
    base = plan((box(0, 0, 10, 10), {"Mesh": "Triangular", "useEleA": 1, "maxEleA": 2}))
    assert base.regions[0].max_area == 2
    assert generate_basepoly(base).report["max_paving_area"] <= 2 * (1 + 1e-7)


def test_outside_structure_preserved_when_requested():
    source = quad(8, 2, 12, 8)
    base = plan((box(0, 0, 10, 10), {"Max_Ela": 2}))
    with pytest.raises(MeshError, match="entirely inside"):
        generate_basepoly(base, structure=source)
    mesh = generate_basepoly(base, structure=source, allow_structure_outside=True)
    assert mesh.report["outside_structure_area"] == 12
    assert mesh.report["domain_area"] == 112
    out = read_2dm(write_2dm(mesh))
    xyz = dict(zip(out.node_ids, out.nodes))
    for nid, node in zip(source.node_ids, source.nodes):
        np.testing.assert_array_equal(xyz[nid], node)


def test_structure_in_adjacent_exclude_feature_is_allowed():
    base = plan((box(0, 0, 5, 10), {"Max_Ela": 2}), (box(5, 0, 10, 10), {"Mesh": "Exclude"}))
    mesh = generate_basepoly(base, structure=quad(5, 0, 10, 10))
    assert mesh.report["domain_area"] == 100
    assert mesh.report["connected_components"] == 1


def test_element_budget_enforced():
    with pytest.raises(MeshError, match="budget"):
        generate_basepoly(plan((box(0, 0, 100, 100), {"Max_Ela": 1})), max_elements=100)


def test_fast_profile_still_enforces_area_and_compact_sidecar():
    mesh = generate_basepoly(plan((box(0, 0, 20, 20), {"useEleA": 1, "maxEleA": 1})),
                             structure=quad(5, 5, 15, 15), minimum_angle=0)
    assert mesh.report["angle_target"] == 0
    assert mesh.report["max_paving_area"] <= 1 * (1 + 1e-7)
    data = json.loads(write_attributes(mesh, compact=True))
    assert data["schema"] == "paveforge-attributes-v2"
    assert len(data["node_ids"]) == len(mesh.nodes)
    for c, i in zip(mesh.cells, data["element_ids"]):
        if c.structure:
            assert data["element_attributes"][str(i)] == ["0.0025", "12"]


def test_paving_subset_has_only_used_nodes():
    from paveforge.model import select_cells
    mesh = generate_basepoly(plan((box(0, 0, 20, 20), {"Max_Ela": 2})), structure=quad(5, 5, 15, 15))
    paving = select_cells(mesh, structure=False)
    assert all(not c.structure for c in paving.cells)
    assert {n for c in paving.cells for n in c.nodes} == set(range(len(paving.nodes)))
    assert len(read_2dm(write_2dm(paving)).cells) == mesh.report["paving_triangles"]


def test_repaired_structure_export_orders_ids_for_sms_reader(tmp_path):
    import py2dm
    mesh = generate_basepoly(plan((box(0, 0, 20, 20), {"Max_Ela": 1})),
                             structure=quad(5, 5, 15, 15, eid=1, nid=1), minimum_angle=0)
    output = tmp_path / "ordered.2dm"
    output.write_text(write_2dm(mesh))
    with py2dm.Reader(output) as independent:
        assert independent.num_nodes == len(mesh.nodes)
        assert independent.num_elements == len(mesh.cells)
    from paveforge.model import select_cells
    paving = select_cells(mesh, structure=False, preserve_ids=False)
    paving_output = tmp_path / "unstruct.2dm"
    paving_output.write_text(write_2dm(paving))
    with py2dm.Reader(paving_output) as independent:
        assert independent.num_nodes == len(paving.nodes)
        assert independent.num_elements == len(paving.cells)
