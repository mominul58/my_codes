from pathlib import Path

from streamlit.testing.v1 import AppTest


APP = Path(__file__).resolve().parents[1] / "app.py"


def test_example_workflow_and_stale_exports():
    app = AppTest.from_file(str(APP)).run(timeout=20)
    assert not app.exception
    app.button[0].click().run(timeout=30)
    assert not app.exception
    assert not app.error
    assert app.metric[3].value == "1"
    assert len(app.get("download_button")) == 4
    app.number_input[0].set_value(6.0).run()
    assert not app.exception
    assert len(app.get("download_button")) == 0
    assert any("Inputs changed" in e.value for e in app.info)


def test_invalid_polygon_shows_error_and_no_exports():
    app = AppTest.from_file(str(APP)).run()
    app.checkbox[0].uncheck()
    app.text_area[0].set_value("0,0\n10,10\n0,10\n10,0")
    app.button[0].click().run()
    assert not app.exception
    assert app.error
    assert len(app.get("download_button")) == 0


def test_example_basepoly_field_workflow():
    app = AppTest.from_file(str(APP)).run()
    app.checkbox[1].check()
    app.button[0].click().run(timeout=30)
    assert not app.exception and not app.error
    mesh = app.session_state["result"][1]
    assert mesh.report["domain_area"] == 9475
    assert mesh.report["intentional_holes"] == 1
    assert len(app.get("download_button")) == 4


def test_exclude_only_shows_message_and_no_downloads():
    app = AppTest.from_file(str(APP)).run()
    app.checkbox[0].uncheck()
    app.text_area[0].set_value('{"type":"Feature","geometry":{"type":"Polygon","coordinates":[[[0,0],[10,0],[10,10],[0,10],[0,0]]]},"properties":{"Mesh":"Exclude"}}')
    app.button[0].click().run()
    assert not app.exception and not app.error
    assert len(app.get("download_button")) == 0
    assert any("No Triangular" in item.value for item in app.info)


def test_merge_requires_input_mesh():
    app = AppTest.from_file(str(APP)).run()
    app.radio[0].set_value("Merge existing 2DM meshes").run()
    app.button[0].click().run()
    assert not app.exception
    assert any("Upload an existing paving" in item.value for item in app.error)


def test_angle_failures_block_mesh_downloads_and_offer_diagnostics():
    app = AppTest.from_file(str(APP)).run()
    app.checkbox[0].uncheck()
    app.text_area[0].set_value("0,0\n10,0\n10,0.1")
    app.button[0].click().run(timeout=30)
    assert not app.exception
    assert app.session_state["result"][1].report["export_blocked"]
    labels = [item.label for item in app.get("download_button")]
    assert labels == ["Download angle failures (.csv)", "Download validation report (.json)"]
    assert any("export is blocked" in item.value for item in app.error)


def test_transition_merge_controls_match_previous_tool():
    app = AppTest.from_file(str(APP)).run()
    app.radio[0].set_value("Merge existing 2DM meshes").run()
    controls = {item.label: item.value for item in app.number_input}
    assert controls["Selection distance (m)"] == 8
    assert controls["Material ID to mark erased base elements"] == 99
    assert controls["Maximum area for fill elements (m²)"] == 10
    assert app.text_input[0].value == "EPSG:25832"
