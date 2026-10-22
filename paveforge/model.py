from dataclasses import dataclass, field
from dataclasses import replace
from math import floor

import numpy as np


class MeshError(ValueError):
    """An input or mesh cannot safely satisfy the conforming-mesh contract."""


def default_weld_tolerance(span):
    # Metre-based GIS workflows commonly compare nodes at millimetre precision.
    # Keep very small synthetic domains usable as well.
    return max(1e-10, min(.001, span * 1e-5))


def edge_precision(nodes):
    """Collinearity depends on coordinate precision, not node merge distance."""
    scale = float(np.abs(np.asarray(nodes)[:, :2]).max())
    return max(1e-12, float(np.spacing(scale)) * 16)


@dataclass(frozen=True)
class Cell:
    nodes: tuple[int, ...]
    material: int = 1
    structure: bool = False
    attributes: tuple[str, ...] = ()
    id: int | None = None
    region: int | None = None


@dataclass
class Mesh:
    nodes: np.ndarray
    cells: list[Cell]
    warnings: list[str] = field(default_factory=list)
    report: dict = field(default_factory=dict)
    node_ids: list[int] | None = None
    node_attributes: list[tuple[str, ...]] | None = None


def select_cells(mesh: Mesh, *, structure: bool, preserve_ids: bool = True) -> Mesh:
    """Extract paving/structure without exporting unused nodes or changing values."""
    cells = [c for c in mesh.cells if c.structure == structure]
    used = sorted({n for c in cells for n in c.nodes})
    mapping = {old: new for new, old in enumerate(used)}
    return Mesh(mesh.nodes[used].copy(), [replace(c, nodes=tuple(mapping[n] for n in c.nodes), id=c.id if preserve_ids else None) for c in cells],
                list(mesh.warnings), {"subset_of_validated_mesh": True, "nodes": len(used), "elements": len(cells), "ids_renumbered": not preserve_ids,
                                      "export_blocked": mesh.report.get("export_blocked", False)},
                [mesh.node_ids[n] for n in used] if preserve_ids and mesh.node_ids else None,
                [mesh.node_attributes[n] for n in used] if mesh.node_attributes else None)


class NodeIndex:
    """Tolerance-based XY welding with neighbor buckets, including bucket borders."""

    def __init__(self, tolerance: float):
        self.tolerance = tolerance
        self.nodes: list[tuple[float, float, float]] = []
        self.buckets: dict[tuple[int, int], list[int]] = {}

    def add(self, x: float, y: float, z: float = 0, *, check_z: bool = False) -> int:
        key = (floor(x / self.tolerance), floor(y / self.tolerance))
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for i in self.buckets.get((key[0] + dx, key[1] + dy), []):
                    a, b, c = self.nodes[i]
                    if (x - a)**2 + (y - b)**2 <= self.tolerance**2:
                        if check_z and abs(z - c) > self.tolerance:
                            raise MeshError("Coincident structure nodes have conflicting elevations.")
                        return i
        i = len(self.nodes)
        self.nodes.append((float(x), float(y), float(z)))
        self.buckets.setdefault(key, []).append(i)
        return i
