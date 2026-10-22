import json
import shlex

import numpy as np
from shapely.geometry import Polygon, shape

from .model import Cell, Mesh, MeshError


def read_polygon(text: str) -> Polygon:
    """Read one GeoJSON Polygon or lines of x,y coordinates; retain intentional holes."""
    try:
        if text.lstrip().startswith("{"):
            obj = json.loads(text)
            if obj.get("type") == "FeatureCollection":
                if len(obj["features"]) != 1:
                    raise MeshError("Supply exactly one polygon feature.")
                obj = obj["features"][0]
            if obj.get("type") == "Feature":
                obj = obj["geometry"]
            polygon = shape(obj)
        else:
            points = []
            for line in text.splitlines():
                line = line.split("#", 1)[0].strip()
                if not line:
                    continue
                row = line.replace(",", " ").split()
                if len(row) != 2:
                    raise MeshError("Coordinate rows must contain exactly x and y.")
                points.append(tuple(map(float, row)))
            polygon = Polygon(points)
        if polygon.geom_type != "Polygon":
            raise MeshError("Use one connected Polygon, not a MultiPolygon or another geometry.")
        if polygon.has_z:
            raise MeshError("Use 2D polygon coordinates; structure elevations come from the 2DM.")
        if not polygon.is_valid or polygon.is_empty or polygon.area <= 0:
            raise MeshError("Polygon must be nonempty, simple, and have positive area.")
        if not np.isfinite(np.asarray(polygon.exterior.coords)).all():
            raise MeshError("Polygon coordinates must be finite.")
        return polygon
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        if isinstance(exc, MeshError):
            raise
        raise MeshError(f"Cannot read polygon: {exc}") from exc


def read_2dm(text: str) -> Mesh:
    """Read SMS geometry, original IDs, node values and all element attribute tokens."""
    nodes = {}
    node_attributes = {}
    records = []
    element_ids = set()
    ignored = set()
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith(("#", "!", "//")):
            continue
        try:
            row = shlex.split(line) if '"' in line or "'" in line else line.split()
            if not row:
                continue
            tag = row[0].upper()
            if tag == "ND":
                if len(row) < 5:
                    raise MeshError("ND needs an ID and x y z.")
                i = int(row[1])
                if i <= 0 or i in nodes:
                    raise MeshError("Node IDs must be unique positive integers.")
                nodes[i] = tuple(float(x.replace("D", "E").replace("d", "e")) for x in row[2:5])
                node_attributes[i] = tuple(row[5:])
            elif tag in ("E3T", "E4Q"):
                count = 3 if tag == "E3T" else 4
                if len(row) < count + 3:
                    raise MeshError(f"{tag} needs ID, {count} node IDs, and material ID.")
                i = int(row[1])
                if i <= 0 or i in element_ids:
                    raise MeshError("Element IDs must be unique positive integers.")
                element_ids.add(i)
                records.append((tuple(map(int, row[2:2 + count])), int(row[2 + count]), tuple(row[3 + count:]), i))
            elif tag.startswith("E") and len(tag) > 1 and tag[1].isdigit():
                raise MeshError(f"Unsupported element {tag}; use E3T or E4Q planar cells.")
            elif tag != "MESH2D":
                ignored.add(tag)
        except (ValueError, IndexError) as exc:
            raise MeshError(f"2DM line {number}: {exc}") from exc
    if not nodes or not records:
        raise MeshError("Structure 2DM needs ND nodes and E3T or E4Q elements.")
    ids = {i: n for n, i in enumerate(nodes)}
    used = {i for row, *_ in records for i in row}
    if used - nodes.keys():
        raise MeshError("An element references a missing node ID.")
    if nodes.keys() - used:
        raise MeshError("Structure contains unused ND nodes. Remove or attach them before import.")
    xyz = np.asarray(list(nodes.values()), dtype=float)
    if not np.isfinite(xyz).all():
        raise MeshError("Structure coordinates must be finite.")
    warnings = []
    if ignored:
        warnings.append("Geometry, node values and element attributes are imported. Other 2DM records are omitted: " + ", ".join(sorted(ignored)))
    return Mesh(xyz, [Cell(tuple(ids[i] for i in row), material, True, attributes, eid) for row, material, attributes, eid in records],
                warnings, node_ids=list(nodes), node_attributes=list(node_attributes.values()))


def write_2dm(mesh: Mesh) -> str:
    if not mesh.cells:
        raise MeshError("No triangular features were selected; no 2DM is generated.")
    node_ids, element_ids = export_ids(mesh)
    lines = ["MESH2D"]
    # Ordered IDs improve compatibility with SMS readers that scan IDs in sequence.
    for n in sorted(range(len(mesh.cells)), key=element_ids.__getitem__):
        i, cell = element_ids[n], mesh.cells[n]
        tag = "E3T" if len(cell.nodes) == 3 else "E4Q"
        tail = " ".join((str(cell.material), *cell.attributes))
        lines.append(f"{tag} {i} " + " ".join(str(node_ids[n]) for n in cell.nodes) + f" {tail}")
    for n in sorted(range(len(mesh.nodes)), key=node_ids.__getitem__):
        i, (x, y, z) = node_ids[n], mesh.nodes[n]
        extra = mesh.node_attributes[n] if mesh.node_attributes else ()
        tail = " " + " ".join(extra) if extra else ""
        lines.append(f"ND {i} {x:.17g} {y:.17g} {z:.17g}{tail}")
    return "\n".join(lines) + "\n"


def export_ids(mesh: Mesh):
    """Keep source IDs; allocate new IDs beyond the reserved source range."""
    def allocate(preferred):
        used = {i for i in preferred if i is not None}
        if any(i <= 0 for i in used) or len(used) != sum(i is not None for i in preferred):
            raise MeshError("Export IDs must be unique positive integers.")
        next_id = max(used, default=0) + 1
        output = []
        for i in preferred:
            if i is None:
                i = next_id
                next_id += 1
            output.append(i)
        return output
    return allocate(mesh.node_ids if mesh.node_ids is not None else [None] * len(mesh.nodes)), allocate([c.id for c in mesh.cells])


def write_attributes(mesh: Mesh, *, compact: bool = False) -> str:
    """Sidecar preserves attributes/IDs that Medit's single reference cannot encode."""
    node_ids, element_ids = export_ids(mesh)
    if compact:
        return json.dumps({
            "schema": "paveforge-attributes-v2",
            "ordering": "node_ids and element_ids follow in-memory/Medit vertex order; Medit cells use medit_element_order. Native 2DM records are sorted by their IDs.",
            "node_ids": node_ids,
            "node_attributes": {str(i): list(mesh.node_attributes[n]) for n, i in enumerate(node_ids)
                                if mesh.node_attributes and mesh.node_attributes[n]},
            "element_ids": element_ids,
            "element_materials": [c.material for c in mesh.cells],
            "element_attributes": {str(i): list(c.attributes) for i, c in zip(element_ids, mesh.cells) if c.attributes},
            "structure_element_ids": [i for i, c in zip(element_ids, mesh.cells) if c.structure],
            "basepoly_feature_ids": [c.region for c in mesh.cells],
            "medit_element_order": [i for size in (3, 4) for i, c in zip(element_ids, mesh.cells) if len(c.nodes) == size],
            "report": mesh.report, "warnings": mesh.warnings,
        }, separators=(",", ":"))
    return json.dumps({
        "nodes": [{"id": i, "xyz": xyz.tolist(), "attributes": list(mesh.node_attributes[n]) if mesh.node_attributes else []}
                  for n, (i, xyz) in enumerate(zip(node_ids, mesh.nodes))],
        "elements": [{"id": i, "nodes": [node_ids[n] for n in c.nodes], "material": c.material,
                      "attributes": list(c.attributes), "structure": c.structure, "basepoly_feature": c.region}
                     for i, c in zip(element_ids, mesh.cells)],
        "medit_order": [i for size in (3, 4) for i, c in zip(element_ids, mesh.cells) if len(c.nodes) == size],
        "report": mesh.report, "warnings": mesh.warnings,
    }, indent=2)


def write_medit(mesh: Mesh) -> str:
    """Medit .mesh with 3D vertices, planar connectivity, and material references."""
    lines = ["MeshVersionFormatted 2", "Dimension 3", "Vertices", str(len(mesh.nodes))]
    lines.extend(f"{x:.17g} {y:.17g} {z:.17g} 0" for x, y, z in mesh.nodes)
    for size, tag in ((3, "Triangles"), (4, "Quadrilaterals")):
        cells = [c for c in mesh.cells if len(c.nodes) == size]
        if cells:
            lines.extend([tag, str(len(cells))])
            lines.extend(" ".join(str(n + 1) for n in c.nodes) + f" {c.material}" for c in cells)
    return "\n".join(lines + ["End", ""])
