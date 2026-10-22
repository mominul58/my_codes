# PaveForge

A Python mesh studio for creating triangular unstructured meshes from a basepoly shapefile and merging them with SMS `.2dm` structure meshes. Run it as a browser app or a command-line tool.

For Windows, the portable download is published under [Releases](https://github.com/mominul58/my_codes/releases) only after its Windows build and smoke tests pass. Download `PaveForge-Windows.zip`, extract the entire folder, and open **PaveForge.exe**. It includes Python and dependencies; your installed Python version does not matter. A desktop window starts the local dashboard and opens your browser. Keep the desktop window open while working. See `PORTABLE_README.txt` inside the source checkout or `READ_ME_FIRST.txt` in the portable package.

To run from source on Windows, extract the source ZIP and open **START_WINDOWS.bat**. It supports standard Python **3.12 and 3.13**, automatically installs/repairs dependencies when necessary, then opens the desktop dashboard. `INSTALL_WINDOWS.bat` can force a repair. Startup errors remain visible, and logs are available from the desktop window. On Mac/Linux run `bash START_MAC_LINUX.sh`. See `QUICKSTART.txt` for input examples.

## Get started

Source installation supports Python 3.12 and 3.13. Dependencies are locked across Windows, macOS, and Linux with platform markers and artifact hashes.

```bash
cd /workspace/my_codes
python3 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements.lock
.venv/bin/python -m pip install --no-deps -e .
.venv/bin/python -m streamlit run app.py --server.address 0.0.0.0 --server.port 8501
```

On another computer, run these commands from your checkout instead of `/workspace/my_codes`. Runtime dependencies are pinned with artifact hashes in `requirements.lock`; test dependencies are separate in `requirements-dev.lock`, and executable build tools in `requirements-build.lock`. No accounts, API keys, database, or external service are needed at runtime.

In the app, select **Create unstructured mesh**, upload basepoly, optionally import a structure `.2dm`, set the fallback maximum triangle area, and select **Generate unstructured mesh**. The example structure is enabled initially; turn it off when using your own input. Download individual mesh files or a ZIP containing `.2dm`, Medit `.mesh`, and the full attributes/report JSON. Results and exports are cached rather than recomputed on every interaction.

## Input formats

**Shapefile:** ZIP one layer's matching `.shp`, `.dbf`, `.shx`, `.prj`, and optionally `.cpg` files together. The ZIP is read in memory without extraction. `examples/basepoly-shapefile.zip` is a working example. Multipart features and adjacent polygon regions are supported.

| Basepoly field | Meaning |
| --- | --- |
| `Mesh = Triangular` | Generate triangular paving inside this feature. |
| `Mesh = Exclude` or blank | Leave the area empty. Do not create a separate 2DM for it. |
| `use_Ela = 1` or `1.0` | Apply this feature's `Max_Ela`; the default is 1. |
| `use_Ela = 0` | Ignore its `Max_Ela` and use the app's fallback area limit. |
| `Max_Ela` | Maximum generated triangle area in **m²**, not edge length. |

Field names and Mesh values are case-insensitive. If `Max_Ela` is absent/blank, the fallback area is used. A plain polygon without a Mesh field is treated as Triangular. Unknown Mesh values and invalid enabled area values are rejected. Exclude features may overlay Triangular features; overlapping Triangular features are rejected to avoid ambiguous sizing. A layer containing only Exclude/blank features produces no mesh file.

The shapefile aliases **`useEleA`** and **`maxEleA`** are supported too. A supplied file with `useEleA=0` uses the fallback even when maxEleA is populated; the app does not silently change the enable flags. The **Basepoly attribute check** shows the original fields, resolved mesh mode, and effective maximum area before generation. The dashboard/CLI fallback now defaults to **40 m²**, matching `a40` in the previous tool; enabled per-feature areas still override it.

**Other polygon input:** GeoJSON Polygon/MultiPolygon, Feature, FeatureCollection, or plain text with one `x,y` pair per line:

```text
0, 0
100, 0
100, 80
0, 80
```

The last point can repeat the first. Interior rings and Exclude polygons remain empty of paving. Intentionally separate basepoly areas can produce multiple mesh components; validation requires the component count to match the reference domain. Use the same projected XY coordinate system with metre units for all inputs. Geographic or feet-based CRS declarations are rejected; absent CRS declarations produce a warning and assume metres. Longitude/latitude are not automatically reprojected. No CRS information is inferred from `.2dm`.

**Structure:** SMS `MESH2D` with `ND id x y z`, `E3T id n1 n2 n3 material [additional attributes...]`, and/or `E4Q id n1 n2 n3 n4 material [additional attributes...]`. Node IDs can be nonsequential; elements can precede nodes. Original XYZ node values, material IDs, additional element attribute tokens, and extended ND attributes are retained. Original IDs are preserved where unique. Welding duplicate coordinates or resolving collisions between two files necessarily remaps some IDs; mappings are included in the report. If a retained cell must split, child cells inherit all parent attributes; the first child keeps the original ID. Boundary conditions, node strings, and other nongeometry records are omitted with a warning. Unsupported element types are rejected.

## How merging works

1. Validate the basepoly and source elements. Weld coincident/near-coincident coordinates within the reported tolerance, keeping the first representative node's exact XYZ. Conflicting elevations or node attributes at a shared node are rejected.
2. Subtract Exclude areas, preserve all regional boundaries, and build one planar straight-line graph with the structure edges.
3. Use native Triangle constrained Delaunay meshing with regional attributes and per-region maximum areas, followed by adaptive area refinement near structures. No buffer border is inserted as an extra constrained edge. Interface segments may subdivide so generated paving actually meets `Max_Ela` within relative numerical tolerance of 1e-7; it is not just a size suggestion.
4. Split source cells when required by new/hanging interface nodes. Children retain the parent material and every additional attribute. Unaffected convex quads/triangles remain intact. New interface Z values interpolate along original edges; original source node values stay unchanged.
5. Validate before enabling export: coverage against the reference domain, no overlapping/outside area, no duplicate/orphan/hanging nodes, manifold edges, and the expected connected-component count. Explicit holes and Exclude areas are excluded from paving coverage. A structure supplied inside an excluded footprint is merged back as explicit structure elements, without generating paving over it.

Area constraints apply to **new paving triangles**, not existing input elements whose values/attributes must be retained. **Refine paving near structures** defaults to a **10 m²** maximum within a configurable **10 m** distance. Where enabled regional limits and the structure-band limit both apply, the smaller area wins. Original structure cells keep their sizes unless connectivity repair splits them.

Every triangle and quadrilateral is checked against **5° minimum / 160° maximum interior angles** by default, including source elements and reflex quad angles. The app/CLI block mesh exports when any cell fails. The preview, validation JSON, and a CSV listing failing exported element IDs remain available. Explicitly disable strict acceptance in the dashboard or use `--diagnostic-export` only when you want a diagnostic mesh with known angle failures. The Python API reports angles by default; pass `enforce_angle_limits=True` to apply the same export gate.

**Efficient · 10° target** is the default generation profile. **Previous tool · 26° target** matches `q26`; Balanced targets 20° and Fast omits angle refinement. These are Triangle generation targets, separate from measured acceptance limits. Quality refinement can require more time and elements. Source connectivity repair is enabled in the dashboard/CLI: bounded local retriangulation retains original node XYZ and copies parent attributes to child cells, with interpolated Z for new interior nodes. Two bounded passes may remesh adjacent cells with identical material, attributes, source type, and region; changed interfaces are conformed again. Original IDs are retained, and patch parent/replacement IDs are recorded. Original fixed acute corners and attribute boundaries can make angle limits impossible even after repair; the app reports these failures instead of claiming compliance. The Python API retains a legacy length-based `generate_mesh` entry point and a mode that keeps fixed interface elements.

Advanced controls include **Keep constraint segments fixed (previous tool: YY)** and **Align basepoly to structure within**. Alignment defaults to **0.10 m** for generation and **0.11 m** for the merge dashboard, inspired by the supplied tools' cleanup distances. It projects nearby polygon vertices onto immutable structure edges and snaps nearby corners; it is an independently implemented alignment operation, rather than a replacement for every ArcGIS cleanup function. The report gives changed-part counts and symmetric-difference area. Original source XYZ remains fixed. Set alignment to **0** for exact basepoly geometry. A cleanup that collapses a feature is rejected. Field selectors in the attribute check can override automatic Mesh/useEleA/maxEleA detection.

Meshing is planar in XY. Original source Z values are retained; new noninterface paving nodes use the configured constant elevation (default zero). This is not a terrain interpolation or 3D volume mesher. Near-coincident nodes welded together use one original representative XY; the alias mapping records the change. Automatic welding uses **1 mm** for large metre-based domains and a smaller distance for very small domains. Validation checks the **full** reported weld distance. Boundaries are noded again after welding, and actual graph faces receive regional seeds. Use a smaller explicit tolerance when submillimetre features must remain separate.

Overlapping, self-intersecting, degenerate, or incompatible-value input elements are rejected. Unused input ND records must be removed or attached to elements. The default budget is 2,000,000 output elements, configurable up to 5,000,000; large jobs require more memory. The preview samples large meshes for responsiveness while exports retain every element.

The app's **Keep outside structure** setting preserves imported structure extending beyond the basepoly and reports the added area in the merged reference footprint. It does not create paving outside Triangular features or automatically bridge existing isolated source pieces. Disable it to require the structure to lie inside basepoly. The CLI/API require `--keep-outside-structure` / `allow_structure_outside=True` to enable this behavior. File uploads support up to 250 MB per file; shapefile ZIP contents are additionally capped at 100 MB uncompressed.

## Fast merge of existing 2DM files

Select **Merge existing 2DM meshes** and upload the unstructured base mesh plus the road/trench structure. **Erase and refill around roads / trenches** is enabled initially, with controls matching the previous tool:

| Control | Default / behavior |
| --- | --- |
| Selection distance | **8 m**. Select whole base elements at this distance or closer to the structure, including overlapping elements. |
| Erase material marker | **99**. Record selected elements under this marker, then remove them. Existing material-99 cells elsewhere remain intact. |
| Fill element maximum area | **10 m²**. Refill the uncovered footprint with conforming triangles. Smaller enabled basepoly limits remain effective. |
| Fill material | **1**, configurable independently from the erase marker. |
| Coordinate system | **EPSG:25832 — ETRS89 / UTM zone 32N**. Declares the common input CRS; coordinates are not reprojected. |

Unselected base cells and structure cells retain their node values and attributes; interface/quality repairs may create children with parent attributes. The report records erased IDs and original materials/attributes. Without basepoly, coverage is checked against the **original base footprint plus structure**, retaining existing holes. With basepoly, its Triangular/Exclude rules define the fill domain.

Turn off erase/refill for a direct merge. Conforming files then use node welding and validation without paving. Hanging-node interfaces use a repair fallback that splits affected cells and retains their attributes. Matching coordinates with incompatible elevations/attributes are rejected because one shared node cannot preserve two different values.

For direct merging, supply basepoly to detect and fill accidental gaps. Without it, direct-merge coverage is checked against the input cell union. Erase/refill retains the original base footprint as its reference even when no basepoly is supplied. Input node/element ID collisions are resolved with complete maps; structure IDs take precedence.

## Command line

```bash
.venv/bin/paveforge examples/basepoly-shapefile.zip \
  --max-area 25 \
  --structure examples/structure.2dm \
  --output outputs/example
```

For the road/trench transition workflow (basepoly is optional):

```bash
.venv/bin/paveforge --merge-paving basemesh.2dm --structure roads-trenches.2dm \
  --transition --selection-distance 8 --erase-material 99 --fill-area 10 \
  --crs EPSG:25832 --keep-outside-structure --output outputs/merged
```

Generation controls include `--near-structure-area 10 --near-structure-distance 10`,
`--angle-min 5 --angle-max 160`, and `--min-angle 26` for the previous tool's generation target. Add `--fixed-segments` for its `YY` behavior and `--boundary-cleanup 0.11` for merge alignment (CLI default 0.10 m).
An angle failure saves `.json` and `.angle-failures.csv` diagnostics and exits with status 2 without writing mesh files.

This creates `outputs/example.2dm`, `.mesh`, `.json`, and `.attributes.json`. Omit `--structure` for paving only. Optional controls: `--tolerance`, `--elevation`, and `--material`. `--max-area` is the fallback; per-feature enabled Max_Ela values override it. Legacy `--size` converts a length to equilateral-triangle area. Validation failures exit with status 2 before writing a new result. Successful runs overwrite the selected output paths. Exclude-only runs write no output.

```bash
.venv/bin/paveforge examples/basepoly-shapefile.zip \
  --merge-paving your-unstruct.2dm --structure your-structure.2dm \
  --max-area 25 --output outputs/merged
```

Medit output uses `MeshVersionFormatted 2`, `Dimension 3`, 3D vertices, Triangles and Quadrilaterals. It supports one material reference per element; it cannot directly carry every extended SMS attribute. Keep the attributes JSON sidecar with the Medit file. The compact v2 sidecar maps file-order nodes/elements to exported IDs and includes all additional attribute tokens, materials, structure IDs, feature IDs, Medit element ordering, and validation data. XYZ and connectivity are already present in the mesh file and are not duplicated in this compact sidecar. Native 2DM retains extended attributes directly. With structures present, the app's ZIP also includes a paving-only `unstruct.2dm` with unused nodes removed.

Merged SMS records are sorted by their preserved IDs for reader compatibility. Paving-only subset files use fresh consecutive IDs while retaining the same XYZ values, connectivity, and attributes; there are no source structure elements to preserve in that standalone paving file. **`merged.2dm` is the complete mesh, including structure.** Use that file directly. The ZIP's `unstruct.2dm` is an alternative subset, rather than an additional part to combine with `merged.2dm`. The ZIP includes `angle-failures.csv` (header only when all elements pass).

## Development and validation

Build the Windows executable on Windows with `BUILD_WINDOWS.bat`, or let the `Windows desktop app` GitHub Actions workflow build it. The workflow tests Python 3.13 and the source installer, bundles Python with PyInstaller, then exercises the frozen native mesh engine and a desktop window with a real local HTTP readiness check. It publishes a portable ZIP only after those checks pass. Linux PyInstaller cannot produce a Windows executable.

Desktop source launch: `python desktop.py`. Headless mesh/bundle diagnostic: `python desktop.py --self-test`. Source-install logs are under `%LOCALAPPDATA%\PaveForge\logs` on Windows. Incomplete environments are renamed as `.venv-backup-*` rather than deleted. The first source launch requires network access for installation; portable releases run locally without installation.

```bash
.venv/bin/python -m pip install --require-hashes -r requirements-dev.lock
.venv/bin/python -m pytest -q
.venv/bin/python -m pip check
```

Tests exercise shapefile field parsing/CRS, strict regional areas, exclusions, original node values and extended attributes, fast merging and repair, size refinement, boundary contacts, coverage, invalid inputs, exports, and app workflows. Standard Medit/SMS exports are also checked with independent meshio/py2dm readers. Extended nonstandard SMS attributes are additionally checked with exact round trips.

Vectorized geometry checks and a verified coverage-union path replace per-edge query loops. Cached results and prepared export buffers avoid repeated meshing/serialization when interacting with the preview. Performance depends on geometry and hardware; elapsed meshing/validation time is shown in the app.

To intentionally refresh the dependency lock:

```bash
uv pip compile pyproject.toml --universal --generate-hashes \
  --output-file requirements.lock --python-version 3.12
uv pip compile pyproject.toml --extra dev --universal --generate-hashes \
  --output-file requirements-dev.lock --python-version 3.12
uv pip compile requirements-build.in --universal --generate-hashes \
  --output-file requirements-build.lock --python-version 3.12
```

Build a portable source archive with `.venv/bin/python scripts/package.py /tmp/PaveForge.zip`. It excludes the virtual environment, caches, outputs, and Git metadata. The cloud task already has its own isolated checkout. Use it; do not create another Git worktree unless explicitly requested. Restart Streamlit in a new task: live processes do not survive snapshots.

Triangle and its Python wrapper have their own license terms; check the installed dependency notices before distributing them, especially for commercial use.
