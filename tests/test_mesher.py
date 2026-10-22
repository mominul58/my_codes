from pathlib import Path

import numpy as np
import pytest
from shapely.geometry import Polygon, box

from paveforge import generate_mesh, read_2dm, read_polygon, write_2dm, write_medit
from paveforge.model import MeshError, NodeIndex


def structure_from_faces(faces, z=0):
    """Deliberately use different node IDs per face to exercise coordinate welding."""
    lines = ["MESH2D"]
    n = 1
    for i, face in enumerate(faces, 1):
        ids = list(range(n, n + len(face)))
        lines.append(f"{'E3T' if len(face) == 3 else 'E4Q'} {i} " + " ".join(map(str, ids)) + " 7")
        for x, y in face:
            lines.append(f"ND {n} {x} {y} {z}")
            n += 1
    return read_2dm("\n".join(lines))


def assert_sound(mesh, area):
    r = mesh.report
    assert r["connected_components"] == 1
    assert r["gap_area"] < 1e-7
    assert r["overlap_area"] < 1e-7
    assert r["outside_area"] < 1e-7
    assert r["domain_area"] == pytest.approx(area)
    assert r["duplicate_nodes"] == r["unused_nodes"] == r["hanging_nodes"] == 0
    # Connectivity independently checked in the exported/reimported representation.
    exported = read_2dm(write_2dm(mesh))
    assert len(exported.nodes) == len(mesh.nodes)
    assert len(exported.cells) == len(mesh.cells)
    np.testing.assert_allclose(exported.nodes, mesh.nodes)
    for cell in mesh.cells:
        xy = mesh.nodes[list(cell.nodes), :2]
        signed_area = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(xy, np.roll(xy, -1, axis=0)))
        assert signed_area > 0


def test_plain_polygon_and_size_control():
    coarse = generate_mesh(box(0, 0, 20, 20), 5)
    fine = generate_mesh(box(0, 0, 20, 20), 2)
    assert_sound(coarse, 400)
    assert_sound(fine, 400)
    assert fine.report["paving_triangles"] > 3 * coarse.report["paving_triangles"]
    assert fine.report["max_paving_area"] <= fine.report["target_area"] * 1.01


def test_interior_quads_weld_shared_nodes_and_preserve_materials():
    structure = structure_from_faces([
        [(4, 4), (8, 4), (8, 8), (4, 8)],
        [(8, 4), (12, 4), (12, 8), (8, 8)],
    ], z=3)
    mesh = generate_mesh(box(0, 0, 20, 20), 2, structure)
    assert_sound(mesh, 400)
    retained = [c for c in mesh.cells if c.structure]
    assert len(retained) == 2
    assert all(len(c.nodes) == 4 and c.material == 7 for c in retained)
    assert len({n for c in retained for n in c.nodes}) == 6
    assert all(mesh.nodes[n, 2] == 3 for c in retained for n in c.nodes)


def test_hanging_structure_node_is_automatically_connected():
    structure = structure_from_faces([
        [(2, 2), (8, 2), (8, 8), (2, 8)],
        [(8, 2), (12, 2), (12, 5), (8, 5)],
        [(8, 5), (12, 5), (12, 8), (8, 8)],
    ], z=2)
    mesh = generate_mesh(box(0, 0, 20, 20), 2, structure)
    assert_sound(mesh, 400)
    assert any("Triangulated" in w for w in mesh.warnings)


def test_boundary_touching_structure():
    structure = structure_from_faces([[(0, 3), (6, 3), (6, 9), (0, 9)]], z=4)
    mesh = generate_mesh(box(0, 0, 20, 20), 2, structure)
    assert_sound(mesh, 400)
    for c in mesh.cells:
        if c.structure:
            assert all(mesh.nodes[n, 2] == 4 for n in c.nodes)


def test_structure_fills_entire_domain():
    structure = structure_from_faces([[(0, 0), (10, 0), (10, 10), (0, 10)]])
    mesh = generate_mesh(box(0, 0, 10, 10), 2, structure)
    assert_sound(mesh, 100)
    assert mesh.report["paving_triangles"] == 0


def test_intentional_hole_and_concave_domain():
    domain = Polygon([(0, 0), (20, 0), (20, 10), (15, 10), (15, 20), (0, 20)],
                     holes=[[(3, 3), (6, 3), (6, 6), (3, 6)]])
    mesh = generate_mesh(domain, 2)
    assert_sound(mesh, domain.area)
    assert mesh.report["intentional_holes"] == 1


def test_multiple_separate_structures_join_through_paving():
    structure = structure_from_faces([
        [(2, 2), (5, 2), (5, 5)],
        [(12, 12), (16, 12), (16, 16), (12, 16)],
    ])
    assert_sound(generate_mesh(box(0, 0, 20, 20), 2, structure), 400)


def test_conflicting_elevations_rejected():
    structure = read_2dm("MESH2D\nE3T 1 1 2 3 7\nE3T 2 4 5 6 7\n"
                         "ND 1 2 2 0\nND 2 4 2 0\nND 3 4 4 0\n"
                         "ND 4 4 2 1\nND 5 6 2 0\nND 6 4 4 0\n")
    with pytest.raises(MeshError, match="conflicting elevations"):
        generate_mesh(box(0, 0, 20, 20), 2, structure)


def test_hanging_elevation_conflict_rejected():
    structure = structure_from_faces([
        [(2, 2), (8, 2), (8, 8), (2, 8)],
        [(8, 5), (12, 4), (12, 6)],
    ])
    structure.nodes[4, 2] = 2
    with pytest.raises(MeshError, match="conflicting elevations"):
        generate_mesh(box(0, 0, 20, 20), 2, structure)


@pytest.mark.parametrize("faces, message", [
    ([[(2, 2), (8, 2), (8, 8), (2, 8)], [(4, 4), (10, 4), (10, 10), (4, 10)]], "overlap"),
    ([[(-1, 2), (4, 2), (4, 4)]], "entirely inside"),
    ([[(2, 2), (8, 8), (2, 8), (8, 2)]], "self-intersecting"),
])
def test_invalid_structure_rejected(faces, message):
    with pytest.raises(MeshError, match=message):
        generate_mesh(box(0, 0, 20, 20), 2, structure_from_faces(faces))


@pytest.mark.parametrize("size", [0, -1, float("nan"), float("inf"), 0.00001])
def test_invalid_or_unreasonable_size_rejected(size):
    with pytest.raises(MeshError):
        generate_mesh(box(0, 0, 20, 20), size)


def test_node_welding_across_bucket_boundaries():
    index = NodeIndex(0.1)
    assert index.add(.099, 0) == index.add(.101, 0)


def test_medit_export_readable_by_independent_meshio(tmp_path):
    import meshio

    mesh = generate_mesh(box(0, 0, 10, 10), 3, structure_from_faces([[(3, 3), (6, 3), (6, 6), (3, 6)]]))
    lines = write_medit(mesh).splitlines()
    assert lines[:3] == ["MeshVersionFormatted 2", "Dimension 3", "Vertices"]
    assert int(lines[3]) == len(mesh.nodes)
    for tag, count in [("Triangles", sum(len(c.nodes) == 3 for c in mesh.cells)), ("Quadrilaterals", 1)]:
        start = lines.index(tag)
        assert int(lines[start + 1]) == count
        for row in lines[start + 2:start + 2 + count]:
            values = list(map(int, row.split()))
            assert min(values[:-1]) >= 1 and max(values[:-1]) <= len(mesh.nodes)
    assert lines[-1] == "End"
    path = tmp_path / "export.mesh"
    path.write_text(write_medit(mesh))
    independent = meshio.read(path)
    np.testing.assert_allclose(independent.points, mesh.nodes)
    assert len(independent.cells_dict["triangle"]) == mesh.report["paving_triangles"]
    assert len(independent.cells_dict["quad"]) == 1
    assert 7 in independent.cell_data["medit:ref"][-1]


def test_2dm_export_independent_reader(tmp_path):
    import py2dm

    mesh = generate_mesh(box(0, 0, 10, 10), 3)
    path = tmp_path / "export.2dm"
    path.write_text(write_2dm(mesh))
    with py2dm.Reader(str(path)) as independent:
        assert independent.num_nodes == len(mesh.nodes)
        assert independent.num_elements == len(mesh.cells)
        np.testing.assert_allclose([n.pos for n in independent.nodes], mesh.nodes)


def test_near_duplicate_structure_nodes_welded():
    structure = structure_from_faces([
        [(4, 4), (8, 4), (8, 8), (4, 8)],
        [(8.00000002, 4), (12, 4), (12, 8), (8.00000002, 8)],
    ])
    mesh = generate_mesh(box(0, 0, 20, 20), 2, structure, tolerance=1e-6)
    assert_sound(mesh, 400)
    assert len({n for c in mesh.cells if c.structure for n in c.nodes}) == 6


@pytest.mark.parametrize("text", ["0,0\n10,10\n0,10\n10,0", '{"type":"MultiPolygon","coordinates":[]}', "hello"])
def test_invalid_polygon_rejected(text):
    with pytest.raises(MeshError):
        read_polygon(text)


def test_example_files():
    root = Path(__file__).resolve().parents[1]
    mesh = generate_mesh(read_polygon((root / "examples/domain.geojson").read_text()), 8,
                         read_2dm((root / "examples/structure.2dm").read_text()))
    assert_sound(mesh, 9700)
