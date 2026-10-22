import argparse
import json
from pathlib import Path

from .formats import read_2dm, write_2dm, write_medit, write_attributes
from .basepoly import read_basepoly
from .mesher import generate_basepoly
from .merge import merge_meshes
from .transition import merge_with_transition
from .model import MeshError
from .quality import angle_failures_csv


def main():
    parser = argparse.ArgumentParser(description="Generate validated triangular paving around a structure mesh.")
    parser.add_argument("polygon", type=Path, nargs="?", help="Zipped basepoly shapefile, GeoJSON, or x,y coordinate text")
    sizing = parser.add_mutually_exclusive_group()
    sizing.add_argument("--size", type=float, help="Legacy target length; converted to an equilateral-triangle area")
    sizing.add_argument("--max-area", type=float, default=None, help="Default maximum area in m² (default 40); enabled Max_Ela fields override it")
    parser.add_argument("--merge-paving", type=Path, help="Merge this existing paving 2DM with --structure")
    parser.add_argument("--structure", type=Path, help="Optional SMS structure .2dm")
    parser.add_argument("--output", type=Path, default=Path("outputs/paving"), help="Output filename prefix")
    parser.add_argument("--tolerance", type=float, default=None, help="Coordinate welding precision")
    parser.add_argument("--elevation", type=float, default=0, help="Elevation of new paving nodes")
    parser.add_argument("--material", type=int, default=1, help="Material ID for paving")
    parser.add_argument("--element-budget", type=int, default=2_000_000, help="Maximum output elements (default 2000000)")
    parser.add_argument("--keep-outside-structure", action="store_true", help="Retain source structure beyond basepoly and report the extended merged footprint")
    parser.add_argument("--min-angle", type=float, default=10., help="Triangle generation target (default 10); use 26 to match the previous tool, or 0 for area-only")
    parser.add_argument("--angle-min", type=float, default=5., help="Required minimum interior angle, every element (default 5)")
    parser.add_argument("--angle-max", type=float, default=160., help="Required maximum interior angle, every element (default 160)")
    parser.add_argument("--diagnostic-export", action="store_true", help="Explicitly permit export when the quality report contains angle failures")
    parser.add_argument("--no-source-angle-repair", action="store_true", help="Keep source connectivity unless interface conformity requires splitting")
    parser.add_argument("--near-structure-area", type=float, default=10., help="Maximum paving area in the structure refinement band (default 10 m²)")
    parser.add_argument("--near-structure-distance", type=float, default=10., help="Width of structure refinement band (default 10 m)")
    parser.add_argument("--no-near-refinement", action="store_true")
    parser.add_argument("--fixed-segments", action="store_true", help="Triangle YY mode: prohibit constrained-segment splitting, as in the previous merge script")
    parser.add_argument("--boundary-cleanup", type=float, default=.1, help="Align basepoly vertices to fixed source edges within this distance (default 0.1 m; use 0 for exact polygon geometry)")
    parser.add_argument("--transition", action="store_true", help="With --merge-paving, erase base cells near structures and refill the transition")
    parser.add_argument("--selection-distance", type=float, default=8., help="Transition selection distance in metres (default 8)")
    parser.add_argument("--erase-material", type=int, default=99, help="Audit marker for erased base cells (default 99)")
    parser.add_argument("--fill-area", type=float, default=10., help="Maximum transition fill area in m² (default 10)")
    parser.add_argument("--fill-material", type=int, default=1, help="Material for new transition cells (default 1)")
    parser.add_argument("--crs", default="EPSG:25832", help="Common transition input CRS, no reprojection (default ETRS89 / UTM 32N)")
    args = parser.parse_args()
    try:
        if args.polygon is None and not args.merge_paving:
            raise MeshError("Supply a basepoly file or --merge-paving.")
        if args.size is not None and args.size <= 0:
            raise MeshError("Element size must be positive.")
        plan = read_basepoly(args.polygon.read_bytes(), args.polygon.name) if args.polygon else None
        structure = read_2dm(args.structure.read_text()) if args.structure else None
        if args.transition and not args.merge_paving:
            raise MeshError("--transition requires --merge-paving and --structure.")
        area = args.max_area if args.max_area is not None else (3**.5 * args.size**2 / 4 if args.size is not None else 40.)
        options = dict(max_elements=args.element_budget, allow_structure_outside=args.keep_outside_structure,
                       minimum_angle=args.min_angle, angle_min=args.angle_min, angle_max=args.angle_max,
                       enforce_angle_limits=not args.diagnostic_export, repair_source_angles=not args.no_source_angle_repair,
                       near_structure_area=None if args.no_near_refinement else args.near_structure_area,
                       near_structure_distance=args.near_structure_distance, fixed_segments=args.fixed_segments,
                       boundary_cleanup_distance=args.boundary_cleanup)
        if args.merge_paving:
            if structure is None:
                raise MeshError("--merge-paving requires --structure.")
            if args.transition:
                mesh = merge_with_transition(read_2dm(args.merge_paving.read_text()), structure, plan,
                                             tolerance=args.tolerance, selection_distance=args.selection_distance,
                                             erase_material=args.erase_material, fill_area=args.fill_area,
                                             fill_material=args.fill_material, coordinate_system=args.crs, **options)
            else:
                mesh = merge_meshes(read_2dm(args.merge_paving.read_text()), structure, plan, area, args.tolerance, **options)
        else:
            mesh = generate_basepoly(plan, area, structure, args.tolerance, args.material, args.elevation,
                                     **options)
        if not mesh.cells:
            print(mesh.report["message"])
            return
        args.output.parent.mkdir(parents=True, exist_ok=True)
        report = {**mesh.report, "warnings": mesh.warnings}
        Path(str(args.output) + ".json").write_text(json.dumps(report, indent=2) + "\n")
        Path(str(args.output) + ".angle-failures.csv").write_text(angle_failures_csv(mesh))
        if mesh.report.get("export_blocked"):
            raise MeshError("Angle acceptance failed; diagnostics were saved, and mesh export was blocked. See the .json and .angle-failures.csv files.")
        Path(str(args.output) + ".2dm").write_text(write_2dm(mesh))
        Path(str(args.output) + ".mesh").write_text(write_medit(mesh))
        Path(str(args.output) + ".attributes.json").write_text(write_attributes(mesh, compact=True) + "\n")
        print(json.dumps(report, indent=2))
    except (MeshError, OSError) as exc:
        parser.exit(2, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
