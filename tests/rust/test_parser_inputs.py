"""Input packaging, unit scopes and physical stackup provenance."""
import gzip
import tarfile
import zipfile

import pytest
from shapely.geometry import box

from conftest import write_tree
from odb_fixture import exported_layers, odb_files
from pcbkit_rust.job import load_job
from tools.audit_out import physical_shape


@pytest.mark.parametrize("packaging", ["directory", "tar", "tgz", "zip"])
def test_job_packaging_and_gzip_content_preserve_material_geometry(tmp_path, packaging):
    files = odb_files([{"name": "CU", "features": (
        "UNITS=MM\nF 1\n$0 custom\nP 5 5 0 P 0 0\n"
    )}])
    files["symbols/custom/features"] = "UNITS=MM\nF 1\n$0 s2000\nP 0 0 0 P 0 0\n"
    root = write_tree(tmp_path / "source" / "job", files)
    # Compression is detected from content even without a .gz filename suffix.
    feature_path = root / "steps/board/layers/cu/features"
    feature_path.write_bytes(gzip.compress(feature_path.read_bytes()))
    if packaging == "directory":
        source = root
    elif packaging in ("tar", "tgz"):
        source = tmp_path / ("input." + packaging)
        with tarfile.open(source, "w:gz" if packaging == "tgz" else "w") as archive:
            archive.add(root, arcname="wrapped/job")
    else:
        source = tmp_path / "input.zip"
        with zipfile.ZipFile(source, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for relative in files:
                archive.write(root / relative, "wrapped/job/" + relative)
    job = load_job(str(source))
    assert physical_shape(exported_layers(job)["CU"]["polygons"]).equals(box(4, 4, 6, 6))
    assert not job.diagnostics.errors


@pytest.mark.parametrize("job_units,file_units,marker,diameter", [
    ("MM", "INCH", "", .254),
    ("INCH", "MM", "", .01),
    ("MM", "INCH", "M", .01),
    ("INCH", "MM", "I", .254),
])
def test_file_coordinates_and_symbol_size_override_have_distinct_scopes(
    tmp_path, job_units, file_units, marker, diameter
):
    files = odb_files([{"name": "CU", "features": (
        f"UNITS={file_units}\nF 1\n$0 s10 {marker}\nP 1 2 0 P 0 0\n"
    )}], units=job_units)
    job = load_job(str(write_tree(tmp_path / "job", files)))
    record = job.single_step.layers[0].features.records[0]
    scale = 25.4 if file_units == "INCH" else 1
    assert (record.x_mm, record.y_mm) == pytest.approx((scale, 2 * scale))
    shape = physical_shape(exported_layers(job)["CU"]["polygons"])
    assert shape.bounds == pytest.approx((scale - diameter / 2, 2 * scale - diameter / 2,
                                         scale + diameter / 2, 2 * scale + diameter / 2), abs=1e-4)


def test_missing_units_inherit_imperial_default_and_report_location(tmp_path):
    files = odb_files([{"name": "CU", "features": "F 1\n$0 s10\nP 1 2 0 P 0 0\n"}], units=None)
    job = load_job(str(write_tree(tmp_path / "job", files)))
    assert job.units.system.value == "INCH" and not job.info.units_declared
    record, = job.single_step.layers[0].features.records
    assert (record.x_mm, record.y_mm) == pytest.approx((25.4, 50.8))
    issues = {issue.code: issue for issue in job.diagnostics.warnings}
    assert issues["info.missing_units"].path.endswith("misc/info")
    assert issues["features.no_units"].path.endswith("layers/cu/features")
    shape = physical_shape(exported_layers(job)["CU"]["polygons"])
    assert shape.bounds == pytest.approx((25.273, 50.673, 25.527, 50.927))


@pytest.mark.parametrize("units,size,expected", [("MM", 300, .3), ("INCH", 10, .254)])
def test_drill_tools_use_size_units_and_tool_declared_plating(tmp_path, units, size, expected):
    tools = f"UNITS={units}\n" + "\n".join(
        f"TOOLS {{\nNUM={number}\nTYPE={kind}\nFINISH_SIZE={size}\nDRILL_SIZE={size + 2}\n}}"
        for number, kind in [(1, "PLATED"), (2, "NON_PLATED")]
    )
    files = odb_files([{"name": "DRILL_PLATED", "type": "DRILL", "tools": tools}],
                      units="INCH" if units == "MM" else "MM")
    job = load_job(str(write_tree(tmp_path / "job", files)))
    layer, = job.single_step.layers
    for number in (1, 2):
        tool = layer.tool_for(number)
        assert tool.finish_size_mm == pytest.approx(expected)
        assert tool.drill_size_mm == pytest.approx(expected + 2 * (.001 if units == "MM" else .0254))
        assert tool.type.is_plated == (number == 1)


@pytest.mark.parametrize("units,weight,copper_mm,dielectric,dielectric_mm,kind", [
    ("MM", 35, .035, .2, .2, "thickness_um"),
    ("INCH", 1, 28.349523125 / (30.48 ** 2) / 8.96 * 10, .01, .254, "areal_density_oz_ft2"),
])
def test_attribute_file_units_determine_thickness_and_export_provenance(
    tmp_path, units, weight, copper_mm, dielectric, dielectric_mm, kind
):
    files = odb_files([
        {"name": "TOP", "attrlist": f"UNITS={units}\n.copper_weight={weight}\n"},
        {"name": "FR", "type": "DIELECTRIC", "attrlist": f"UNITS={units}\n.layer_dielectric={dielectric}\n"},
        {"name": "BOTTOM", "attrlist": "UNITS=MM\n.copper_weight=18\n"},
        {"name": "D", "type": "DRILL", "matrix": {"START_NAME": "TOP", "END_NAME": "BOTTOM"}},
    ], units="INCH" if units == "MM" else "MM")
    job = load_job(str(write_tree(tmp_path / "job", files)))
    layers = exported_layers(job)
    assert layers["TOP"]["z_mm"]["end_mm"] == pytest.approx(copper_mm, abs=1e-7)
    assert layers["FR"]["z_mm"]["start_mm"] == pytest.approx(copper_mm, abs=1e-7)
    assert layers["FR"]["z_mm"]["end_mm"] == pytest.approx(copper_mm + dielectric_mm, abs=1e-7)
    assert layers["BOTTOM"]["z_mm"]["start_mm"] == pytest.approx(copper_mm + dielectric_mm, abs=1e-7)
    assert layers["BOTTOM"]["z_mm"]["end_mm"] == pytest.approx(copper_mm + dielectric_mm + .018, abs=1e-7)
    assert all(layers[name]["z_mm"]["thickness_known"] for name in ("TOP", "FR", "BOTTOM"))
    thickness = layers["TOP"]["thickness"]
    assert thickness["known"] and thickness["kind"] == kind
    assert thickness["input_value"] == weight
    assert layers["FR"]["thickness"]["unit"] == ("mm" if units == "MM" else "inch")
    if units == "INCH":
        assert thickness["unit"] == "oz/ft2"
        assert thickness["thickness_source"] == "assumed_density"
        assert thickness["assumed_density_g_cm3"] == 8.96
    else:
        assert thickness["unit"] == "um" and thickness["thickness_source"] == "attribute"
    assert "z_mm" not in layers["D"]
    assert (layers["D"]["start_layer"], layers["D"]["end_layer"]) == ("TOP", "BOTTOM")


@pytest.mark.parametrize("kind,position_known", [("DIELECTRIC", False), ("SILK_SCREEN", True)])
def test_unknown_structural_thickness_propagates_z_uncertainty_but_decoration_does_not(
    tmp_path, kind, position_known
):
    files = odb_files([
        {"name": "TOP", "attrlist": "UNITS=MM\n.copper_weight=35\n"},
        {"name": "GAP", "type": kind},
        {"name": "BOTTOM", "attrlist": "UNITS=MM\n.copper_weight=18\n"},
    ])
    job = load_job(str(write_tree(tmp_path / "job", files)))
    layers = exported_layers(job)
    assert not layers["GAP"]["z_mm"]["thickness_known"]
    bottom = layers["BOTTOM"]
    assert bottom["thickness"]["known"]
    assert bottom["z_mm"]["thickness_known"] == position_known
    assert bottom["z_mm"]["start_mm"] == pytest.approx(.035)
    assert bottom["z_mm"]["end_mm"] == pytest.approx(.053)
