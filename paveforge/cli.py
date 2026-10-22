import argparse
import json
from pathlib import Path

from .formats import read_2dm, write_2dm, write_medit, write_attributes
from .basepoly import read_basepoly
from .mesher import generate_basepoly
from .merge import merge_meshes
from .model import MeshError


def main():
    parser = argparse.ArgumentParser(description="Generate validated triangular paving around a structure mesh.")
    parser.add_argument("polygon", type=Path, nargs="?", help="Zipped basepoly shapefile, GeoJSON, or x,y coordinate text")
    sizing = parser.add_mutually_exclusive_group()
    sizing.add_argument("--size", type=float, help="Legacy target length; converted to an equilateral-triangle area")
    sizing.add_argument("--max-area", type=float, default=None, help="Default maximum area in m² (default 25); enabled Max_Ela fields override it")
    parser.add_argument("--merge-paving", type=Path, help="Merge this existing paving 2DM with --structure")
    parser.add_argument("--structure", type=Path, help="Optional SMS structure .2dm")
    parser.add_argument("--output", type=Path, default=Path("outputs/paving"), help="Output filename prefix")
    parser.add_argument("--tolerance", type=float, default=None, help="Coordinate welding precision")
    parser.add_argument("--elevation", type=float, default=0, help="Elevation of new paving nodes")
    parser.add_argument("--material", type=int, default=1, help="Material ID for paving")
    parser.add_argument("--element-budget", type=int, default=2_000_000, help="Maximum output elements (default 2000000)")
    parser.add_argument("--keep-outside-structure", action="store_true", help="Retain source structure beyond basepoly and report the extended merged footprint")
    parser.add_argument("--min-angle", type=float, default=0., help="Angle target in degrees; 0 is fast area-only refinement (default), or use 20/25")
    args = parser.parse_args()
    try:
        if args.polygon is None and not args.merge_paving:
            raise MeshError("Supply a basepoly file or --merge-paving.")
        if args.size is not None and args.size <= 0:
            raise MeshError("Element size must be positive.")
        plan = read_basepoly(args.polygon.read_bytes(), args.polygon.name) if args.polygon else None
        structure = read_2dm(args.structure.read_text()) if args.structure else None
        area = args.max_area if args.max_area is not None else (3**.5 * args.size**2 / 4 if args.size is not None else 25.)
        if args.merge_paving:
            if structure is None:
                raise MeshError("--merge-paving requires --structure.")
            mesh = merge_meshes(read_2dm(args.merge_paving.read_text()), structure, plan, area, args.tolerance,
                                max_elements=args.element_budget, allow_structure_outside=args.keep_outside_structure)
        else:
            mesh = generate_basepoly(plan, area, structure, args.tolerance, args.material, args.elevation,
                                     max_elements=args.element_budget, allow_structure_outside=args.keep_outside_structure, minimum_angle=args.min_angle)
        if not mesh.cells:
            print(mesh.report["message"])
            return
        args.output.parent.mkdir(parents=True, exist_ok=True)
        Path(str(args.output) + ".2dm").write_text(write_2dm(mesh))
        Path(str(args.output) + ".mesh").write_text(write_medit(mesh))
        report = {**mesh.report, "warnings": mesh.warnings}
        Path(str(args.output) + ".json").write_text(json.dumps(report, indent=2) + "\n")
        Path(str(args.output) + ".attributes.json").write_text(write_attributes(mesh, compact=True) + "\n")
        print(json.dumps(report, indent=2))
    except (MeshError, OSError) as exc:
        parser.exit(2, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
