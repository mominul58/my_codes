import hashlib
import json
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

import plotly.graph_objects as go
import streamlit as st

from paveforge import generate_basepoly, read_basepoly, merge_meshes, merge_with_transition, read_2dm, write_2dm, write_medit
from paveforge.formats import write_attributes, export_ids
from paveforge.model import MeshError, select_cells
from paveforge.quality import angle_failures_csv

ROOT = Path(__file__).resolve().parent
st.set_page_config(page_title="PaveForge · Mesh Studio", page_icon="◬", layout="wide")
st.markdown("""<style>
.stApp { background: #0b1020; }
[data-testid="stSidebar"] { background: #121a2c; }
.hero {padding: 1rem 0 1.5rem;}
.eyebrow {color:#5eead4; font-size:.78rem; letter-spacing:.22em; font-weight:700;}
.hero h1 {font-size:3rem; letter-spacing:-.05em; margin:.3rem 0; color:#eef4ff;}
.hero p {color:#a9b8d1; font-size:1.05rem; max-width:760px;}
[data-testid="stMetric"] {background:#151f34; border:1px solid #28344b; border-radius:12px; padding:14px;}
.stButton > button[kind="primary"] {background:#2dd4bf;color:#09201e;border:0;}
</style>""", unsafe_allow_html=True)

st.markdown("""<div class="hero"><div class="eyebrow">PAVEFORGE / MESH STUDIO</div>
<h1>From boundary to connected mesh.</h1>
<p>Triangular paving, precise structure interfaces, and a validated mesh ready to export.</p></div>""", unsafe_allow_html=True)

with st.sidebar:
    workflow = st.radio("Workflow", ["Create unstructured mesh", "Merge existing 2DM meshes"])
    st.markdown("### Basepoly & mesh inputs")
    st.caption("01 / BASEPOLY")
    polygon_upload = st.file_uploader("Upload basepoly", type=["zip", "geojson", "json", "txt"], help="For shapefiles, ZIP the matching .shp, .dbf, .shx, and .prj files together.")
    default = "0, 0\n120, 0\n120, 65\n95, 65\n95, 85\n0, 85"
    polygon_text = st.text_area("Or enter x, y coordinates", default, height=165)
    st.caption("Projected metre coordinates. Shapefile fields: Mesh, useEleA, maxEleA (use_Ela / Max_Ela also supported).")
    st.divider()
    st.caption("02 / STRUCTURE")
    structure_upload = st.file_uploader("Import structure mesh", type=["2dm"])
    demo_structure = st.checkbox("Use the example structure", value=True, disabled=structure_upload is not None)
    demo_basepoly = st.checkbox("Use example basepoly fields", value=False, disabled=polygon_upload is not None)
    paving_upload = st.file_uploader("Existing unstructured 2DM to merge", type=["2dm"]) if workflow.startswith("Merge") else None
    transition_settings = None
    if workflow.startswith("Merge"):
        rebuild_transition = st.checkbox("Erase and refill around roads / trenches", value=True)
        if rebuild_transition:
            selection_distance = st.number_input("Selection distance (m)", min_value=0.0, value=8.0)
            erase_material = st.number_input("Material ID to mark erased base elements", min_value=1, value=99, step=1)
            fill_area = st.number_input("Maximum area for fill elements (m²)", min_value=0.000001, value=10.0)
            fill_material = st.number_input("Fill material ID", min_value=1, value=1, step=1)
            coordinate_system = st.text_input("Coordinate system", value="EPSG:25832", help="ETRS89 / UTM zone 32N. This declares the common input CRS; it does not transform 2DM coordinates.")
            transition_settings = dict(selection_distance=selection_distance, erase_material=int(erase_material), fill_area=fill_area,
                                       fill_material=int(fill_material), coordinate_system=coordinate_system)
            st.caption("Select whole base cells within 8 m (or your chosen distance), mark them for removal, preserve the roads/trenches, and refill the transition. Material 99 is an erase marker. Remaining cells keep their attributes.")
    st.divider()
    st.caption("03 / PAVING")
    size = st.number_input("Default maximum area (m²)", min_value=0.000001, value=40.0, format="%.6f", help="Enabled maxEleA overrides this. useEleA=0 uses this fallback, including rows with a populated maxEleA. 40 matches a40 in the previous tool.")
    near_enabled = st.checkbox("Refine paving near structures", value=True)
    near_area = st.number_input("Near-structure maximum area (m²)", min_value=0.000001, value=10.0, disabled=not near_enabled)
    near_distance = st.number_input("Refinement distance from structure (m)", min_value=0.001, value=10.0, disabled=not near_enabled, help="Paving within this band uses the smaller of the regional limit and the near-structure limit. Imported structure areas are preserved.")
    st.caption("04 / ELEMENT ANGLES · ALL TRIANGLES & QUADS")
    angle_min = st.number_input("Minimum interior angle (°)", min_value=0.01, max_value=59.0, value=5.0)
    angle_max = st.number_input("Maximum interior angle (°)", min_value=60.0, max_value=179.99, value=160.0)
    strict_angles = st.checkbox("Require angle limits before mesh export", value=True)
    repair_angles = st.checkbox("Repair source connectivity when needed", value=True, help="May triangulate affected cells and add interior nodes. Original node XYZ and all parent element attributes are kept. Fixed acute corners can still prevent compliance.")
    profile = st.selectbox("Meshing profile", ["Efficient · 10° target", "Previous tool · 26° target", "Balanced · 20° target", "Fast · area only"])
    angle = {"Efficient · 10° target": 10., "Previous tool · 26° target": 26., "Balanced · 20° target": 20., "Fast · area only": 0.}[profile]
    st.caption("Targets guide generation. The separate angle checks decide whether every output element passes.")
    with st.expander("Advanced controls"):
        material = st.number_input("Paving material ID", min_value=1, value=1, step=1)
        elevation = st.number_input("New paving node elevation", value=0.0)
        custom_tolerance = st.checkbox("Set weld tolerance manually")
        tolerance = st.number_input("Node merge distance (m)", min_value=1e-12, value=.001, format="%.9f") if custom_tolerance else None
        st.caption("Automatic node merging uses up to 0.001 m (1 mm). Every output node is checked at the full reported merge distance.")
        fixed_segments = st.checkbox("Keep constraint segments fixed (previous tool: YY)", value=False, help="Disable segment subdivision. This can prevent area or angle limits from being met along coarse interfaces. Automatic interface subdivision is recommended for conforming fill.")
        boundary_cleanup = st.number_input("Align basepoly to structure within (m)", min_value=0.0, value=.11 if workflow.startswith("Merge") else .1, format="%.4f", help="Inspired by the old 0.10 / 0.11 m cleanup settings. Aligns nearby polygon vertices to fixed source edges; reports changed footprint area. Original source XYZ stays fixed. Set zero to preserve basepoly geometry exactly.")
        keep_outside = st.checkbox("Keep outside structure", value=True, help="Preserve source structure beyond the basepoly footprint and report the extended merged domain. Paving still stays inside Triangular features.")
        element_budget = st.number_input("Element budget", min_value=100, max_value=5_000_000, value=2_000_000, step=100_000, help="Maximum output elements. Larger jobs need more memory.")
        st.caption("Original source node values and element attributes are preserved. If an interface splits, child elements inherit parent attributes. New interface elevations interpolate along source edges.")
    build = st.button("Merge meshes" if workflow.startswith("Merge") else "Generate unstructured mesh", type="primary", width="stretch")

try:
    domain_data = polygon_upload.getvalue() if polygon_upload else ((ROOT / "examples/basepoly.geojson").read_bytes() if demo_basepoly else polygon_text.encode())
    domain_name = polygon_upload.name if polygon_upload else "basepoly.geojson"
    structure_text = structure_upload.getvalue().decode("utf-8-sig") if structure_upload else ((ROOT / "examples/structure.2dm").read_text() if demo_structure else "")
except UnicodeError:
    st.error("Upload UTF-8 text files.")
    st.stop()

options = dict(near_structure_area=near_area if near_enabled else None, near_structure_distance=near_distance,
               angle_min=angle_min, angle_max=angle_max, enforce_angle_limits=strict_angles, repair_source_angles=repair_angles,
               fixed_segments=fixed_segments, boundary_cleanup_distance=boundary_cleanup)


@st.cache_data(show_spinner=False, max_entries=4)
def load_basepoly(data, filename, mapping=None):
    return read_basepoly(data, filename, field_mapping=mapping)


field_mapping = {}
with st.expander("Basepoly attribute check", expanded=polygon_upload is not None):
    try:
        plan = load_basepoly(domain_data, domain_name)
        fields = sorted({key for r in plan.regions for key in r.properties})
        if fields:
            columns = st.columns(3)
            for column, label, target in zip(columns, ["Meshtype field", "Use maximum area field", "Maximum area field"], ["mesh", "use_ela", "max_ela"]):
                choice = column.selectbox(label, ["Automatic", *fields])
                if choice != "Automatic":
                    field_mapping[target] = choice
            if field_mapping:
                plan = load_basepoly(domain_data, domain_name, field_mapping)
        rows = [{**r.properties, "Feature": r.feature, "Resolved Mesh": r.mode,
                 "Use maximum area": int(r.use_ela),
                 "Effective maximum area (m²)": (r.max_area if r.use_ela and r.max_area is not None else size) if r.mode == "triangular" else None}
                for r in plan.regions]
        st.caption(f"{sum(r.mode == 'triangular' for r in plan.regions):,} Triangular parts; {sum(r.mode == 'exclude' for r in plan.regions):,} Exclude/blank parts. Near-structure refinement can reduce the effective area further.")
        st.dataframe(rows, hide_index=True, width="stretch")
    except MeshError as exc:
        st.error(str(exc))

fingerprint = hashlib.sha256(domain_data + json.dumps([domain_name, structure_text, workflow, size, tolerance, material, elevation, keep_outside, element_budget, angle, options, transition_settings, field_mapping]).encode() + (paving_upload.getvalue() if paving_upload else b"")).hexdigest()


@st.cache_data(show_spinner=False, max_entries=4)
def compute_mesh(data, filename, structure_data, paving_data, max_area, weld, mat, z, merging, outside, budget, quality_angle, settings, transition, mapping):
    structure = read_2dm(structure_data) if structure_data else None
    if merging:
        if not paving_data or structure is None:
            raise MeshError("Upload an existing paving 2DM and a structure 2DM to merge.")
        plan = load_basepoly(data, filename, mapping) if data else None
        if transition is not None:
            return merge_with_transition(read_2dm(paving_data), structure, plan, tolerance=weld, max_elements=budget,
                                         allow_structure_outside=outside, minimum_angle=quality_angle, **settings, **transition)
        return merge_meshes(read_2dm(paving_data), structure, plan, max_area, weld, max_elements=budget, allow_structure_outside=outside, minimum_angle=quality_angle, **settings)
    return generate_basepoly(load_basepoly(data, filename, mapping), max_area, structure, weld, mat, z, max_elements=budget, allow_structure_outside=outside, minimum_angle=quality_angle, **settings)


if build:
    st.session_state.pop("result", None)
    st.session_state.pop("downloads", None)
    try:
        with st.spinner("Constraining edges, generating triangles, and checking connectivity…"):
            merging = workflow.startswith("Merge")
            # In merge mode, pasted demo coordinates are not an implicit boundary.
            # A supplied basepoly is required to infer and fill accidental gaps.
            mesh = compute_mesh(domain_data if not merging or polygon_upload or demo_basepoly else b"", domain_name,
                                structure_text, paving_upload.getvalue().decode("utf-8-sig") if paving_upload else "",
                                size, tolerance, int(material), elevation, merging, keep_outside, int(element_budget), angle, options, transition_settings, field_mapping)
            if mesh.cells and not mesh.report.get("export_blocked"):
                sms, medit, attributes = write_2dm(mesh), write_medit(mesh), write_attributes(mesh, compact=True)
                bundle = BytesIO()
                with ZipFile(bundle, "w", ZIP_DEFLATED) as archive:
                    has_structure = any(c.structure for c in mesh.cells)
                    archive.writestr("merged.2dm" if has_structure else "unstruct.2dm", sms)
                    archive.writestr("merged.mesh" if has_structure else "unstruct.mesh", medit)
                    if has_structure:
                        paving_only = select_cells(mesh, structure=False, preserve_ids=False)
                        if paving_only.cells:
                            archive.writestr("unstruct.2dm", write_2dm(paving_only))
                    archive.writestr("attributes.json", attributes)
                    archive.writestr("angle-failures.csv", angle_failures_csv(mesh))
                st.session_state["downloads"] = (sms, medit, attributes, bundle.getvalue())
            st.session_state["result"] = (fingerprint, mesh)
    except MeshError as exc:
        st.error(str(exc))
    except Exception:
        st.error("Meshing failed unexpectedly. Check the input geometry and server logs.")
        import logging
        logging.exception("Mesh generation failed")

result = st.session_state.get("result")
if result and result[0] != fingerprint:
    st.info("Inputs changed. Generate again to update the preview and exports.")
    result = None

if result:
    mesh = result[1]
    if not mesh.cells:
        st.info(mesh.report["message"])
        st.stop()
    metrics = st.columns(4)
    for column, name, key in zip(metrics, ["Nodes", "Paving triangles", "Structure elements", "Connected regions"], ["nodes", "paving_triangles", "structure_elements", "connected_components"]):
        column.metric(name, f"{mesh.report[key]:,}")
    st.write("")
    st.caption(f"Meshing and validation: {mesh.report.get('elapsed_seconds', 0):.3f} s. Repeated identical requests reuse the cached result.")
    preview, quality = st.tabs(["Mesh workspace", "Validation & quality"])
    with preview:
        nodes_visible = st.toggle("Show nodes", value=False)
        full_preview = st.toggle("Show all mesh elements", value=False) if len(mesh.cells) > 50_000 else True
        stride = max(1, (len(mesh.cells) + 49_999) // 50_000) if not full_preview else 1
        if stride > 1:
            st.caption(f"Preview samples every {stride}th element for responsiveness. Exports include all {len(mesh.cells):,} elements.")
        fig = go.Figure()
        for is_structure, color, label in ((False, "#5eead4", "Triangular paving"), (True, "#fbbf24", "Structure")):
            x, y = [], []
            for cell in mesh.cells[::stride]:
                if cell.structure != is_structure:
                    continue
                ids = list(cell.nodes) + [cell.nodes[0]]
                x.extend([*mesh.nodes[ids, 0], None])
                y.extend([*mesh.nodes[ids, 1], None])
            if x:
                fig.add_trace(go.Scattergl(x=x, y=y, mode="lines", name=label, line=dict(color=color, width=1.5 if is_structure else .7), hoverinfo="skip"))
        if nodes_visible:
            node_stride = max(1, (len(mesh.nodes) + 49_999) // 50_000) if not full_preview else 1
            fig.add_trace(go.Scattergl(x=mesh.nodes[::node_stride, 0], y=mesh.nodes[::node_stride, 1], mode="markers", name="Shared nodes", marker=dict(size=4, color="#e2e8f0"), customdata=export_ids(mesh)[0][::node_stride], hovertemplate="Node %{customdata}<br>x %{x}<br>y %{y}<extra></extra>"))
        fig.update_layout(height=580, template="plotly_dark", paper_bgcolor="#10182a", plot_bgcolor="#10182a", margin=dict(l=20, r=20, t=20, b=20), legend=dict(orientation="h", y=1.04), xaxis=dict(title="X · coordinate units", gridcolor="#202c42"), yaxis=dict(title="Y · coordinate units", scaleanchor="x", scaleratio=1, gridcolor="#202c42"))
        st.plotly_chart(fig, width="stretch")
        st.success("Validated: shared interfaces, no duplicate or hanging nodes, and coverage matching the reference geometry within numerical tolerance. Intentional disconnected basepoly areas are reported.")
    with quality:
        q = mesh.report["quality"]
        st.metric("All-element interior angle range", f"{q['minimum_element_angle']:.4f}° – {q['maximum_element_angle']:.4f}°")
        st.write(f"Checked {q['all_elements_checked']:,} elements. Failing limits: {q['elements_failing_angles']:,} ({q['structure_elements_failing_angles']:,} structure; {q['paving_elements_failing_angles']:,} paving).")
        if q["angle_failure_examples"]:
            st.dataframe(q["angle_failure_examples"], hide_index=True, width="stretch")
        st.json(mesh.report)
        st.caption("Exclude and blank Mesh features remain unmeshed. use_Ela=1 applies that feature's Max_Ela in m²; use_Ela=0 uses the default. Area limits apply to generated paving, not preserved input elements. Shared interfaces are refined as needed.")
    for warning in mesh.warnings:
        st.warning(warning)
    if mesh.report.get("export_blocked"):
        st.error("Mesh export is blocked because some elements fail the required angle limits. Fixed corners may need geometry changes that cannot preserve all original node values. The preview and diagnostics remain available.")
        st.download_button("Download angle failures (.csv)", angle_failures_csv(mesh), "angle-failures.csv", "text/csv")
        st.download_button("Download validation report (.json)", json.dumps({**mesh.report, "warnings": mesh.warnings}, indent=2), "validation.json", "application/json")
        st.stop()
    if not mesh.report["quality"]["angles_passed"]:
        st.error("Diagnostic mesh: strict angle acceptance is disabled and some elements fail the limits. Review the CSV in the result ZIP.")
    st.markdown("### Export your mesh")
    sms, medit, attributes, zipped = st.session_state["downloads"]
    exports = st.columns(3)
    output_name = "merged" if any(c.structure for c in mesh.cells) else "unstruct"
    exports[0].download_button("↓ SMS .2dm", sms, output_name + ".2dm", "text/plain", width="stretch")
    exports[1].download_button("↓ Medit .mesh", medit, output_name + ".mesh", "text/plain", width="stretch")
    exports[2].download_button("↓ Attributes & report", attributes, "attributes.json", "application/json", width="stretch")
    st.download_button("Download all mesh files (.zip)", zipped, "mesh-results.zip", "application/zip")
    if output_name == "merged":
        st.caption("The ZIP also includes unstruct.2dm containing paving/base cells only. Use merged.2dm as the complete result; it already includes the structure and shared interface nodes.")
else:
    st.markdown("### A mesh that fits together")
    st.info("Upload your zipped basepoly shapefile, set the fallback maximum triangle area, then generate. Exclude/blank features produce no paving.")
    a, b, c = st.columns(3)
    a.markdown("**01 · Read basepoly fields**\n\nZIP a shapefile with Mesh, use_Ela, and Max_Ela fields. Adjacent regions are meshed together; excluded areas stay blank.")
    b.markdown("**02 · Preserve your mesh data**\n\nImport E3T triangles and E4Q quads. Keep node values, original IDs where possible, and all element attributes. Merge existing meshes with a separate workflow.")
    c.markdown("**03 · Validate and export**\n\nInspect mesh quality and download SMS 2DM, Medit MESH, and the coverage report.")
    st.caption("Meshing is planar in XY. Use the same projected coordinate system and units for both files. This app does not reproject longitude/latitude or repair overlapping structures.")
