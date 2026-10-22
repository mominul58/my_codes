# PaveForge

A Python mesh studio for creating triangular unstructured meshes from a basepoly shapefile and merging them with SMS `.2dm` structure meshes. Run it as a browser app or a command-line tool.

On Windows, extract the complete download ZIP, install Python 3.12, open `INSTALL_WINDOWS.bat` once, then `START_WINDOWS.bat`. On Mac/Linux run `bash START_MAC_LINUX.sh` from the extracted folder. See `QUICKSTART.txt` for the shortest usage guide. Windows launcher files are provided but have not been executed on Windows in the Linux cloud environment.

## Get started

Python 3.12 or newer is required; Python 3.12 is the tested cloud runtime.

```bash
cd /workspace/my_codes
python3 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements.lock
.venv/bin/python -m pip install --no-deps -e .
.venv/bin/python -m streamlit run app.py --server.address 0.0.0.0 --server.port 8501
```

On another computer, run these commands from your checkout instead of `/workspace/my_codes`. Dependencies are pinned with artifact hashes in `requirements.lock`, including the development/test tools. No accounts, API keys, database, or external service are needed at runtime.

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

The shapefile aliases **`useEleA`** and **`maxEleA`** are supported too. A supplied file with `useEleA=0` uses the fallback even when maxEleA is populated; the app does not silently change the enable flags.

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
3. Use one native Triangle constrained Delaunay call with regional attributes and per-region maximum areas. Interface segments may subdivide so generated paving actually meets `Max_Ela` within relative numerical tolerance of 1e-7; it is not just a size suggestion.
4. Split source cells when required by new/hanging interface nodes. Children retain the parent material and every additional attribute. Unaffected convex quads/triangles remain intact. New interface Z values interpolate along original edges; original source node values stay unchanged.
5. Validate before enabling export: coverage against the reference domain, no overlapping/outside area, no duplicate/orphan/hanging nodes, manifold edges, and the expected connected-component count. Explicit holes and Exclude areas are excluded from paving coverage. A structure supplied inside an excluded footprint is merged back as explicit structure elements, without generating paving over it.

Area constraints apply to **new paving triangles**, not existing input elements whose values/attributes must be retained. The app/CLI default to Fast mode: area constraints without an angle target. Balanced (20°) and Quality (25°) can require substantially more elements near narrow geometry. All profiles retain coverage and topology validation. Acute fixed input corners can still force small angles, which are reported; Fast mode is not a promise of simulation-quality elements. The Python API retains a legacy length-based `generate_mesh` entry point and a mode that keeps fixed interface elements; the app uses strict regional area limits and conforming interface refinement.

Meshing is planar in XY. Original source Z values are retained; new noninterface paving nodes use the configured constant elevation (default zero). This is not a terrain interpolation or 3D volume mesher. Near-coincident nodes welded together use one original representative XY; the alias mapping records the change. Use a smaller tolerance when small features matter.

Overlapping, self-intersecting, degenerate, or incompatible-value input elements are rejected. Unused input ND records must be removed or attached to elements. The default budget is 2,000,000 output elements, configurable up to 5,000,000; large jobs require more memory. The preview samples large meshes for responsiveness while exports retain every element.

The app's **Keep outside structure** setting preserves imported structure extending beyond the basepoly and reports the added area in the merged reference footprint. It does not create paving outside Triangular features or automatically bridge existing isolated source pieces. Disable it to require the structure to lie inside basepoly. The CLI/API require `--keep-outside-structure` / `allow_structure_outside=True` to enable this behavior. File uploads support up to 250 MB per file; shapefile ZIP contents are additionally capped at 100 MB uncompressed.

## Fast merge of existing 2DM files

Select **Merge existing 2DM meshes** and upload paving plus structure files. Conforming files use node welding and validation without triangulation. Hanging-node interfaces use a repair fallback that splits affected cells and retains their attributes. Matching coordinates with incompatible elevations/attributes are rejected because one shared node cannot preserve two different values.

Supply basepoly to detect and fill accidental gaps. Without it, coverage is only checked against the union of supplied cells: the app cannot know whether an existing empty area is intended. This limitation is explicitly reported. Input node/element ID collisions are resolved with complete maps; structure IDs take precedence.

## Command line

```bash
.venv/bin/paveforge examples/basepoly-shapefile.zip \
  --max-area 25 \
  --structure examples/structure.2dm \
  --output outputs/example
```

This creates `outputs/example.2dm`, `.mesh`, `.json`, and `.attributes.json`. Omit `--structure` for paving only. Optional controls: `--tolerance`, `--elevation`, and `--material`. `--max-area` is the fallback; per-feature enabled Max_Ela values override it. Legacy `--size` converts a length to equilateral-triangle area. Validation failures exit with status 2 before writing a new result. Successful runs overwrite the selected output paths. Exclude-only runs write no output.

```bash
.venv/bin/paveforge examples/basepoly-shapefile.zip \
  --merge-paving your-unstruct.2dm --structure your-structure.2dm \
  --max-area 25 --output outputs/merged
```

Medit output uses `MeshVersionFormatted 2`, `Dimension 3`, 3D vertices, Triangles and Quadrilaterals. It supports one material reference per element; it cannot directly carry every extended SMS attribute. Keep the attributes JSON sidecar with the Medit file. The compact v2 sidecar maps file-order nodes/elements to exported IDs and includes all additional attribute tokens, materials, structure IDs, feature IDs, Medit element ordering, and validation data. XYZ and connectivity are already present in the mesh file and are not duplicated in this compact sidecar. Native 2DM retains extended attributes directly. With structures present, the app's ZIP also includes a paving-only `unstruct.2dm` with unused nodes removed.

Merged SMS records are sorted by their preserved IDs for reader compatibility. Paving-only subset files use fresh consecutive IDs while retaining the same XYZ values, connectivity, and attributes; there are no source structure elements to preserve in that standalone paving file.

## Development and validation

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m pip check
```

Tests exercise shapefile field parsing/CRS, strict regional areas, exclusions, original node values and extended attributes, fast merging and repair, size refinement, boundary contacts, coverage, invalid inputs, exports, and app workflows. Standard Medit/SMS exports are also checked with independent meshio/py2dm readers. Extended nonstandard SMS attributes are additionally checked with exact round trips.

Vectorized geometry checks and a verified coverage-union path replace per-edge query loops. Cached results and prepared export buffers avoid repeated meshing/serialization when interacting with the preview. Performance depends on geometry and hardware; elapsed meshing/validation time is shown in the app.

To intentionally refresh the dependency lock:

```bash
uv pip compile pyproject.toml --extra dev --generate-hashes \
  --output-file requirements.lock --python .venv/bin/python
```

Build a portable source archive with `.venv/bin/python scripts/package.py /tmp/PaveForge.zip`. It excludes the virtual environment, caches, outputs, and Git metadata. The cloud task already has its own isolated checkout. Use it; do not create another Git worktree unless explicitly requested. Restart Streamlit in a new task: live processes do not survive snapshots.

Triangle and its Python wrapper have their own license terms; check the installed dependency notices before distributing them, especially for commercial use.
