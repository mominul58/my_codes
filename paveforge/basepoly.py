"""Basepoly readers and field rules shared by the UI and CLI."""
from dataclasses import dataclass, field
from io import BytesIO
import json
from pathlib import Path
from zipfile import ZipFile, BadZipFile

import numpy as np
from shapely.geometry import Polygon, shape
from shapely import STRtree, union_all

from .formats import read_polygon
from .model import MeshError


@dataclass
class Region:
    polygon: Polygon
    feature: int
    mode: str = "triangular"
    use_ela: bool = True
    max_area: float | None = None
    properties: dict = field(default_factory=dict)


@dataclass
class BasePoly:
    regions: list[Region]
    warnings: list[str] = field(default_factory=list)

    @property
    def outer(self):
        # Adjacent Exclude features are part of the allowed project footprint too.
        # They remain absent from paving, but can contain an imported structure.
        return union_all([r.polygon for r in self.regions])

    @property
    def exclusions(self):
        return union_all([r.polygon for r in self.regions if r.mode == "exclude"])

    @property
    def domain(self):
        return self.outer.difference(self.exclusions)


def _check_crs(value):
    from pyproj import CRS
    try:
        crs = CRS.from_user_input(value)
    except Exception as exc:
        raise MeshError("Cannot read the basepoly CRS.") from exc
    if not crs.is_projected or any(abs(a.unit_conversion_factor - 1) > 1e-9 for a in crs.axis_info[:2]):
        raise MeshError("Max_Ela uses m²: reproject basepoly and structures to a projected CRS with metre units first.")


def read_basepoly(data: bytes | str, filename: str = "basepoly.geojson") -> BasePoly:
    """GeoJSON or ZIP with one SHP/DBF pair. ZIP members are never extracted."""
    warnings = []
    try:
        if Path(filename).suffix.lower() == ".zip":
            import shapefile
            with ZipFile(BytesIO(data if isinstance(data, bytes) else data.encode())) as archive:
                if sum(i.file_size for i in archive.infolist()) > 100_000_000:
                    raise MeshError("Uncompressed shapefile ZIP exceeds 100 MB.")
                members = {n.lower(): n for n in archive.namelist() if not n.endswith("/")}
                shp = [n for n in members if n.endswith(".shp") and not n.startswith("__macosx/")]
                if len(shp) != 1:
                    raise MeshError("ZIP must contain exactly one shapefile layer (.shp and matching .dbf).")
                prefix = shp[0][:-4]
                if prefix + ".dbf" not in members:
                    raise MeshError("Shapefile ZIP is missing its matching .dbf field table.")
                encoding = archive.read(members[prefix + ".cpg"]).decode().strip() if prefix + ".cpg" in members else "utf-8"
                if encoding == "65001":
                    encoding = "utf-8"
                kwargs = {ext: BytesIO(archive.read(members[prefix + "." + ext])) for ext in ("shp", "dbf", "shx") if prefix + "." + ext in members}
                with shapefile.Reader(**kwargs, encoding=encoding) as reader:
                    features = [{"type": "Feature", "geometry": s.shape.__geo_interface__, "properties": s.record.as_dict()} for s in reader.iterShapeRecords()]
                if prefix + ".prj" in members:
                    _check_crs(archive.read(members[prefix + ".prj"]).decode())
                else:
                    warnings.append("No .prj provided: coordinates are assumed to be projected metres.")
        else:
            text = data.decode("utf-8-sig") if isinstance(data, bytes) else data
            if not text.lstrip().startswith("{"):
                return BasePoly([Region(read_polygon(text), 1)])
            obj = json.loads(text)
            if obj.get("crs"):
                _check_crs(obj["crs"]["properties"]["name"])
            else:
                warnings.append("No projected CRS declared: coordinates are assumed to be projected metres. Reproject geographic GeoJSON before meshing.")
            features = obj.get("features", []) if obj.get("type") == "FeatureCollection" else [obj if obj.get("type") == "Feature" else {"geometry": obj, "properties": {}}]
        regions = []
        for number, feature in enumerate(features, 1):
            properties = feature.get("properties") or {}
            fields = {str(k).strip().casefold(): v for k, v in properties.items()}
            mode = str(fields.get("mesh") or "").strip().casefold() if "mesh" in fields else "triangular"
            if mode in ("", "exclude"):
                mode = "exclude"
            elif mode != "triangular":
                raise MeshError(f"Feature {number}: Mesh must be Triangular, Exclude, or blank.")
            geometry = shape(feature["geometry"])
            if geometry.geom_type not in ("Polygon", "MultiPolygon") or geometry.is_empty or not geometry.is_valid or geometry.has_z:
                raise MeshError(f"Feature {number}: supply a valid 2D polygon or multipolygon.")
            parts = list(geometry.geoms) if geometry.geom_type == "MultiPolygon" else [geometry]
            raw_use = fields.get("use_ela", fields.get("useelea", 1))
            if raw_use in (None, ""):
                raw_use = 1
            try:
                number_use = float(raw_use)
            except (ValueError, TypeError) as exc:
                raise MeshError(f"Feature {number}: use_Ela must be 0 or 1.") from exc
            if number_use not in (0, 1):
                raise MeshError(f"Feature {number}: use_Ela must be 0 or 1.")
            area = fields.get("max_ela", fields.get("maxelea"))
            area = None if area is None or str(area).strip() == "" else area
            if mode == "triangular" and number_use and area is not None:
                area = float(area)
                if not np.isfinite(area) or area <= 0:
                    raise MeshError(f"Feature {number}: Max_Ela must be positive m².")
            else:
                area = None
            regions.extend(Region(p, number, mode, bool(number_use), area, properties) for p in parts)
        selected = [r.polygon for r in regions if r.mode == "triangular"]
        if selected:
            tree = STRtree(selected)
            for i, p in enumerate(selected):
                for j in tree.query(p, predicate="intersects"):
                    if j > i and p.intersection(selected[j]).area > max(p.area, selected[j].area) * 1e-12:
                        raise MeshError("Triangular basepoly features overlap. Use adjacent, nonoverlapping regions; Exclude polygons may overlay them.")
        return BasePoly(regions, warnings)
    except (ValueError, KeyError, TypeError, BadZipFile, UnicodeError) as exc:
        if isinstance(exc, MeshError):
            raise
        raise MeshError(f"Cannot read basepoly: {exc}") from exc
