"""The shared JSON boundary rejects malformed data before geometry is planned."""
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from pcbkit.intermediate import BoardDocument, read_document, write_document
from pcbeq.equiv import EquivalenceError
from test_equiv import build_document, drill_document, rect, run


def copper_document():
    return build_document(layers=[{"name": "CU", "polygons": [rect(0, 0, 10, 10)]}])


def test_schema_is_generated_from_the_same_model():
    from conftest import REPO_ROOT
    schema = json.loads((REPO_ROOT / "contracts/intermediate.schema").read_text(encoding="utf-8"))
    expected = BoardDocument.model_json_schema()
    expected["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    assert schema == expected
    assert "schema" not in schema["properties"] and "generator" not in schema["properties"]


def test_file_roundtrip_uses_the_shared_contract_and_preserves_equivalence(tmp_path):
    document = copper_document()
    model = BoardDocument.model_validate(document)
    path = tmp_path / "board.json"
    write_document(model, path)
    loaded = read_document(path)
    assert loaded == model
    original, restored = run(document), run(loaded.model_dump(mode="json", exclude_none=True))
    assert original.cells == restored.cells
    assert original.grid.x_lines == restored.grid.x_lines
    assert original.grid.y_lines == restored.grid.y_lines


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), "1.0", True])
def test_coordinates_must_be_finite_numbers(bad):
    document = copper_document()
    document["steps"][0]["layers"][0]["polygons"][0]["points"][0][0] = bad
    with pytest.raises(EquivalenceError, match="points"):
        run(document)


@pytest.mark.parametrize("bad", [[0], [0, 1, 2]])
def test_points_have_exactly_two_coordinates(bad):
    document = copper_document()
    document["steps"][0]["domains"][0]["polygons"][0]["points"][0] = bad
    with pytest.raises(EquivalenceError, match="points"):
        run(document)


def test_missing_island_semantics_are_not_guessed():
    document = copper_document()
    del document["steps"][0]["layers"][0]["polygons"][0]["island"]
    with pytest.raises(EquivalenceError, match="island"):
        run(document)


def test_domain_reference_is_checked_instead_of_using_copper_as_domain():
    document = copper_document()
    document["steps"][0]["layers"][0]["domain_id"] = "missing"
    with pytest.raises(EquivalenceError, match="CU.*unknown domain_id"):
        run(document)


def test_profile_reference_is_not_assumed_from_a_reserved_id():
    document = copper_document()
    step = document["steps"][0]
    step["domains"][0]["id"] = "board-outline"
    step["profile_domain_id"] = "board-outline"
    step["layers"][0]["domain_id"] = "board-outline"
    result = run(document)
    assert not result.fatal and result.cells


@pytest.mark.parametrize("field", ["domains", "layers"])
def test_duplicate_identifiers_are_rejected(field):
    document = copper_document()
    records = document["steps"][0][field]
    records.append(dict(records[0]))
    with pytest.raises(EquivalenceError, match="duplicate"):
        run(document)


def test_reversed_z_is_rejected_but_unknown_zero_thickness_is_valid():
    document = copper_document()
    extent = document["steps"][0]["layers"][0]["z_mm"]
    extent.update(start_mm=1.0, end_mm=0.0)
    with pytest.raises(EquivalenceError, match="end_mm"):
        run(document)
    extent.update(start_mm=0.0, end_mm=0.0, thickness_known=False)
    result = run(document)
    assert not result.fatal
    assert all(not c.thickness_known for c in result.cells)


def test_failed_layer_requires_an_explicit_reason():
    document = copper_document()
    layer = document["steps"][0]["layers"][0]
    layer.update(geometry_status="failed", polygons=[])
    with pytest.raises(EquivalenceError, match="geometry_error"):
        run(document)


def test_imperial_thickness_requires_density_and_physical_quantity():
    document = copper_document()
    layer = document["steps"][0]["layers"][0]
    layer["thickness"] = {"known": True, "source": ".copper_weight",
                          "input_value": 1.0, "kind": "areal_density_oz_ft2", "unit": "oz/ft2"}
    with pytest.raises(EquivalenceError, match="assumed_density"):
        run(document)
    layer["thickness"].update(thickness_source="assumed_density", assumed_density_g_cm3=8.96)
    BoardDocument.model_validate(document)


def test_invalid_file_does_not_reach_geometry(tmp_path):
    path = tmp_path / "invalid.json"
    path.write_text('{"units":"inch","steps":[]}', encoding="utf-8")
    with pytest.raises(ValidationError):
        read_document(path)


def test_nonboard_copper_is_not_a_physical_layer():
    document = copper_document()
    document["steps"][0]["layers"][0]["context"] = "MISC"
    result = run(document)
    assert result.layers[0].status == "skipped"
    assert not result.cells


def test_missing_outline_still_roundtrips_for_parser_diagnostics(tmp_path):
    document = copper_document()
    step = document["steps"][0]
    step.update(profile_domain_id=None, domains=[])
    step["layers"][0]["domain_id"] = None
    model = BoardDocument.model_validate(document)
    path = tmp_path / "no-outline.json"
    write_document(model, path)
    assert read_document(path) == model
    with pytest.raises(EquivalenceError, match="board outline"):
        run(read_document(path).model_dump(mode="json", exclude_none=True))


def test_drill_span_cannot_reference_nonexistent_copper_planes():
    document = copper_document()
    layer = document["steps"][0]["layers"][0]
    layer["copper_index"] = 0
    document["steps"][0]["layers"].append({
        "name": "DRILL", "type": "DRILL", "polygons": [], "copper_span": [0, 99],
    })
    with pytest.raises(EquivalenceError, match="DRILL.*copper_span"):
        run(document)


@pytest.mark.parametrize("known", [False, True])
def test_drill_z_is_rejected_even_when_it_was_known(known):
    document = build_document(layers=[{"name": "DIEL", "type": "DIELECTRIC", "polygons": []}])
    document["steps"][0]["layers"].append({
        "name": "DRILL", "type": "DRILL", "polygons": [rect(4, 4, 6, 6)],
        "z_mm": {"start_mm": 0.0, "end_mm": 0.035, "thickness_known": known},
    })
    with pytest.raises(EquivalenceError, match="DRILL must not carry z_mm"):
        run(document)


def test_drill_roundtrip_derives_absolute_z_from_indexed_copper_not_catalogue_order(tmp_path):
    from pcbkit.diagnostics import Diagnostics
    from pcbeq.equiv import _collect_drills
    document = drill_document(layers=[
        {"name": "DIEL", "type": "DIELECTRIC"},
        {"name": "DRILL", "type": "DRILL", "polygons": [rect(4, 4, 6, 6)]},
    ])
    layers = document["steps"][0]["layers"]
    layers[0]["z_mm"].update(start_mm=1.0, end_mm=1.1)
    layers[1]["z_mm"].update(start_mm=2.5, end_mm=2.6)
    layers.reverse()
    path = tmp_path / "board.json"
    write_document(BoardDocument.model_validate(document), path)
    assert "z_mm" not in json.loads(path.read_text())["steps"][0]["layers"][0]
    loaded = read_document(path)
    drills, fatal = _collect_drills(loaded.internal_document()["steps"][0], Diagnostics())
    assert not fatal and len(drills) == 1
    assert (drills[0].z_start_mm, drills[0].z_end_mm) == (1.0, 2.6)
    assert drills[0].copper_span == (0, 1)
    result = run(loaded.model_dump(mode="json", exclude_none=True))
    assert not result.fatal
    assert next(r for r in result.layers if r.name == "DIEL").drill_overlays == ["DRILL"]


@pytest.mark.parametrize("endpoint", [0, 1])
def test_drill_missing_endpoint_z_is_a_locatable_error(endpoint):
    document = drill_document(layers=[
        {"name": "DIEL", "type": "DIELECTRIC"},
        {"name": "DRILL", "type": "DRILL", "polygons": [rect(4, 4, 6, 6)]},
    ])
    document["steps"][0]["layers"][endpoint].pop("z_mm")
    result = run(document)
    assert result.fatal
    assert any(d.code == "equiv.drill_span_unknown" and "DRILL" in d.message
               and "endpoint" in d.message and "z_mm" in d.message
               for d in result.diagnostics.errors)


@pytest.mark.parametrize("command", ["export"])
def test_invalid_export_data_is_a_cli_diagnostic_not_a_traceback(command, tmp_path, capsys, monkeypatch):
    from conftest import case_path
    from pcbkit.job import load_job
    from pcbkit.cli import main
    job = load_job(str(case_path("minimal-test-odb")))
    job.single_step.layers[0].attributes[".copper_weight"] = "nan"
    monkeypatch.setattr("pcbkit.cli.load_job", lambda *args, **kwargs: job)
    assert main([command, "unused-input", "-o", str(tmp_path)]) == 2
    assert "finite" in capsys.readouterr().err
    assert not list(tmp_path.rglob("*.json"))
    assert not list(tmp_path.rglob("*.csv"))
