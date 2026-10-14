"""SVG and HTML rendering.

The properties that matter here are the ones a viewer silently gets wrong: the
fill rule (``evenodd`` would punch holes at every overlap between two positive
features), the y-flip (SVG y grows downwards, ODB++ y grows upwards), and
self-containment of the HTML page.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import pytest

from odbpp.diagnostics import Diagnostics
from odbpp.export import build_step_preview
from odbpp.job import load_job
from odbpp.svg import (
    SvgOptions,
    layer_colour,
    layer_opacity,
    path_data,
    render_html,
    render_svg,
    write_outputs,
)

from conftest import sample_path

SVG_NS = "{http://www.w3.org/2000/svg}"


@pytest.mark.parametrize("sample", ["minimal-test-odb", "fx_negative_layer"])
def test_html_rows_contain_real_checkbox_elements(sample):
    from html.parser import HTMLParser

    class Rows(HTMLParser):
        def __init__(self):
            super().__init__()
            self.rows = {}
            self.current = None

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == "div" and attrs.get("class") == "row":
                self.current = attrs["data-name"]
                self.rows[self.current] = []
            if tag == "input" and self.current is not None:
                self.rows[self.current].append(attrs)

        def handle_endtag(self, tag):
            if tag == "div":
                self.current = None

    _, preview, _ = preview_of(sample)
    parser = Rows()
    parser.feed(render_html(preview))
    assert set(parser.rows) == {layer.name for layer in preview.layers}
    for name, inputs in parser.rows.items():
        assert len(inputs) == 1
        assert inputs[0]["type"] == "checkbox"
        assert ("checked" in inputs[0]) == (not preview.layer(name).is_empty)


def test_zero_digit_coordinates_preserve_integer_zeros():
    from odbpp.clipper import Ring
    from odbpp.svg import _fmt

    assert [_fmt(v, 0) for v in (10, 100, -20, -0.1, 0, 12.7)] == [
        "10", "100", "-20", "0", "0", "13"]
    ring = Ring(((0, 0), (0, 20), (100, 20), (100, 0)), False)
    assert path_data([ring], 0) == "M0 0L0 20L100 20L100 0Z"


def test_zero_digit_svg_viewport_keeps_its_scale():
    from odbpp.svg import _viewbox

    _, preview, _ = preview_of("minimal-test-odb")
    options = SvgOptions(round_digits=0)
    root = parse(render_svg(preview, options=options))
    from odbpp.export import regularise_preview
    expected = _viewbox(regularise_preview(preview, 0), 0)
    assert list(map(float, root.get("viewBox").split())) == [round(v) for v in expected]


@pytest.mark.parametrize("want_svg,want_html,per_layer", [
    (True, True, True), (False, True, False), (True, False, False),
    (False, False, True), (False, False, False),
])
@pytest.mark.parametrize("digits,include_empty", [(None, True), (2, False), (6, True)])
def test_combined_writer_matches_independent_renderers(
        tmp_path, want_svg, want_html, per_layer, digits, include_empty):
    from pathlib import Path
    from odbpp.export import step_to_json
    import json

    job, preview, _ = preview_of("minimal-test-odb")
    options = SvgOptions(round_digits=digits, include_empty=include_empty)
    outputs = write_outputs(preview, job, str(tmp_path), svg_options=options,
                            want_svg=want_svg, want_html=want_html,
                            per_layer_svg=per_layer)
    assert json.loads(Path(outputs.json).read_text(encoding="utf-8")) == step_to_json(job, preview)
    assert bool(outputs.svg) == want_svg
    assert bool(outputs.html) == want_html
    if want_svg:
        assert Path(outputs.svg).read_text(encoding="utf-8") == render_svg(preview, options=options)
    if want_html:
        assert Path(outputs.html).read_text(encoding="utf-8") == render_html(
            preview, options=options, source=job.source_path)
    if per_layer:
        for filename in outputs.layer_svgs:
            text = Path(filename).read_text(encoding="utf-8")
            name = layer_groups(parse(text))[0].get("data-name")
            assert text == render_svg(preview, options=options, layers=[preview.layer(name)])
    else:
        assert outputs.layer_svgs == []


def preview_of(name: str, **options):
    diagnostics = Diagnostics()
    job = load_job(str(sample_path(name)), diagnostics=diagnostics)
    return job, build_step_preview(job, diagnostics=diagnostics), diagnostics


def parse(svg_text: str):
    return ET.fromstring(svg_text)


def layer_groups(root):
    return [el for el in root.iter()
            if el.tag == SVG_NS + "g" and "odb-layer" in (el.get("class") or "")]


def path_of(group) -> str:
    for child in group:
        if child.tag == SVG_NS + "path":
            return child.get("d") or ""
    return ""


# ---------------------------------------------------------------------------
# structure
# ---------------------------------------------------------------------------

class TestSvgStructure:
    def test_it_is_well_formed_xml(self):
        _job, preview, _diag = preview_of("minimal-test-odb")
        parse(render_svg(preview))

    def test_it_declares_the_svg_namespace(self):
        _job, preview, _diag = preview_of("minimal-test-odb")
        assert render_svg(preview).startswith(
            '<svg xmlns="http://www.w3.org/2000/svg"')

    def test_one_group_per_layer(self):
        _job, preview, _diag = preview_of("minimal-test-odb")
        root = parse(render_svg(preview))
        assert len(layer_groups(root)) == len(preview.layers)

    def test_one_path_per_layer_not_per_ring(self):
        # 22 rings in one element instead of 22 elements: on the rigid-flex board
        # this is the difference between one element and 29,235.
        _job, preview, _diag = preview_of("minimal-test-odb")
        root = parse(render_svg(preview))
        for group in layer_groups(root):
            assert len(list(group)) <= 1

    def test_layer_groups_carry_the_data_the_viewer_needs(self):
        _job, preview, _diag = preview_of("minimal-test-odb")
        for group in layer_groups(parse(render_svg(preview))):
            assert group.get("data-name")
            assert group.get("data-type")
            assert group.get("data-rings") is not None
            assert group.get("data-area") is not None
            assert group.get("data-colour", "").startswith("#")

    def test_layer_groups_carry_absolute_z(self):
        # The drawing has to say where a layer sits in the stack, not only what
        # it looks like from above.
        _job, preview, _diag = preview_of("kicad-kitchen-sink")
        groups = {g.get("data-name"): g for g in
                  layer_groups(parse(render_svg(preview)))}
        copper = groups["F.CU"]
        assert float(copper.get("data-z-start")) == pytest.approx(0.01)
        assert float(copper.get("data-z-end")) == pytest.approx(0.011)
        assert copper.get("data-z-known") == "true"
        dielectric = groups["DIELECTRIC_1"]
        assert float(dielectric.get("data-z-end")) > \
            float(dielectric.get("data-z-start"))

    def test_a_layer_with_no_geometry_still_gets_a_group(self):
        # Keeping the element means the layer list matches the drawing, and the
        # emptiness is visible instead of looking like an omission.
        _job, preview, _diag = preview_of("fx_negative_layer")
        text = render_svg(preview, options=SvgOptions(include_empty=True))
        names = {g.get("data-name") for g in layer_groups(parse(text))}
        assert {l.name for l in preview.layers} == names

    def test_empty_layers_can_be_omitted(self):
        _job, preview, _diag = preview_of("fx_negative_layer")
        text = render_svg(preview, options=SvgOptions(include_empty=False))
        groups = layer_groups(parse(text))
        assert all(int(g.get("data-rings")) > 0 for g in groups)

    def test_a_single_layer_can_be_rendered(self):
        _job, preview, _diag = preview_of("minimal-test-odb")
        top = preview.layer("top")
        root = parse(render_svg(preview, layers=[top]))
        groups = layer_groups(root)
        assert len(groups) == 1
        assert groups[0].get("data-name") == "top"


class TestViewBox:
    def test_the_viewbox_matches_the_drawn_bounds(self):
        _job, preview, _diag = preview_of("minimal-test-odb")
        root = parse(render_svg(preview))
        x0, y0, width, height = (float(v) for v in root.get("viewBox").split())
        bx0, by0, bx1, by1 = preview.bounds
        # A 2% margin, so nothing sits on the edge.
        assert x0 <= bx0 and y0 <= by0
        assert x0 + width >= bx1 and y0 + height >= by1

    def test_the_margin_is_proportional_not_absolute(self):
        _job, preview, _diag = preview_of("minimal-test-odb")
        x0, y0, width, height = (
            float(v) for v in parse(render_svg(preview)).get("viewBox").split())
        bx0, by0, bx1, by1 = preview.bounds
        span = max(bx1 - bx0, by1 - by0)
        assert (bx0 - x0) == pytest.approx(0.02 * span, rel=0.05)


class TestYFlip:
    """Model y grows up; SVG y grows down."""

    def test_there_is_exactly_one_flip_and_no_coordinate_negation(self):
        _job, preview, _diag = preview_of("minimal-test-odb")
        text = render_svg(preview)
        assert text.count("scale(1 -1)") == 1
        flip = re.search(r'<g id="odb-flip" transform="([^"]*)"', text)
        assert flip and "scale(1 -1)" in flip.group(1)

    def test_the_flip_group_is_inside_the_viewer_handle(self):
        # The viewer sets a pan/zoom transform on #odb-root; the flip lives on a
        # child so the viewer never has to reconstruct it.
        _job, preview, _diag = preview_of("minimal-test-odb")
        root = parse(render_svg(preview))
        outer = [el for el in root.iter() if el.get("id") == "odb-root"]
        assert outer and outer[0].get("transform") is None
        inner = [el for el in root.iter() if el.get("id") == "odb-flip"]
        assert inner and "scale(1 -1)" in inner[0].get("transform")

    def test_the_flip_maps_the_model_box_onto_the_viewbox(self):
        _job, preview, _diag = preview_of("minimal-test-odb")
        root = parse(render_svg(preview))
        _x0, y0, _w, height = (float(v) for v in root.get("viewBox").split())
        flip = [el for el in root.iter() if el.get("id") == "odb-flip"][0]
        translate = float(re.search(r"translate\(0 ([-\d.]+)\)",
                                    flip.get("transform")).group(1))

        # The transform is translate(0, T) scale(1, -1), so model y maps to T - y.
        # Model y0 is the viewBox's *bottom* edge and must land on its top edge,
        # and vice versa; the two together pin T with no freedom left.
        assert translate - y0 == pytest.approx(y0 + height)
        assert translate - (y0 + height) == pytest.approx(y0)


class TestFillRule:
    def test_nonzero_is_declared_and_evenodd_never_appears(self):
        # evenodd would punch a hole wherever two positive features overlap.
        _job, preview, _diag = preview_of("minimal-test-odb")
        text = render_svg(preview)
        assert 'fill-rule="nonzero"' in text
        assert "evenodd" not in text

    def test_the_fill_rule_is_on_the_ancestor_of_the_paths(self):
        _job, preview, _diag = preview_of("minimal-test-odb")
        root = parse(render_svg(preview))
        holder = [el for el in root.iter() if el.get("fill-rule") == "nonzero"]
        assert holder
        # It must be an ancestor of at least one layer group.
        for group in layer_groups(root):
            assert group.get("fill-rule") is None


class TestPathData:
    def test_a_path_starts_with_a_moveto_and_ends_with_a_close(self):
        _job, preview, _diag = preview_of("minimal-test-odb")
        for group in layer_groups(parse(render_svg(preview))):
            data = path_of(group)
            if data:
                assert data.startswith("M")
                assert data.endswith("Z")

    def test_every_ring_becomes_its_own_subpath(self):
        _job, preview, _diag = preview_of("minimal-test-odb")
        top = preview.layer("top")
        data = path_data(top.rings, 4)
        assert data.count("M") == len(top.rings)
        assert data.count("Z") == len(top.rings)

    def test_trailing_zeros_are_trimmed(self):
        # 12.7000 -> 12.7 is ~20% of the bytes on a busy layer, for no lost
        # information.
        from odbpp.clipper import region_from_rings

        region = region_from_rings([([(0.0, 0.0), (12.7, 0.0),
                                      (12.7, 10.0), (0.0, 10.0)], False)])
        data = path_data(region.rings, 4)
        assert "12.7" in data
        assert "12.7000" not in data
        assert "0 0" in data
        # The ring is rewound to the spec convention, so its start point is not
        # the one that was authored -- assert on the set of coordinates rather
        # than on the order.
        assert data.count("L") == 3

    def test_negative_zero_is_written_as_zero(self):
        from odbpp.clipper import region_from_rings

        region = region_from_rings([([(-0.00001, 0.0), (1.0, 0.0),
                                      (1.0, 1.0), (0.0, 1.0)], False)])
        assert "-0 " not in path_data(region.rings, 3)

    def test_round_digits_is_respected(self):
        from odbpp.clipper import region_from_rings

        region = region_from_rings([([(0.0, 0.0), (1.23456789, 0.0),
                                      (1.23456789, 1.0), (0.0, 1.0)], False)])
        assert "1.235" in path_data(region.rings, 3)
        assert "1.2346" in path_data(region.rings, 4)

    def test_a_degenerate_ring_produces_nothing(self):
        from odbpp.clipper import Ring

        assert path_data([Ring(((0.0, 0.0), (1.0, 1.0)), False)], 4) == ""


# ---------------------------------------------------------------------------
# colours and opacity
# ---------------------------------------------------------------------------

class TestPresentation:
    def test_colours_are_stable_across_calls(self):
        _job, preview, _diag = preview_of("minimal-test-odb")
        first = [layer_colour(l, i) for i, l in enumerate(preview.layers)]
        second = [layer_colour(l, i) for i, l in enumerate(preview.layers)]
        assert first == second

    def test_colours_depend_on_the_matrix_row_not_the_iteration_index(self):
        # So the same layer is the same colour in the SVG, the HTML list and any
        # per-layer export, regardless of what was filtered out.
        _job, preview, _diag = preview_of("minimal-test-odb")
        top = preview.layer("top")
        assert layer_colour(top, 0) == layer_colour(top, 7)

    def test_same_type_different_rows_are_distinguishable(self):
        _job, preview, _diag = preview_of("kicad-video-odb.tgz")
        signals = [l for l in preview.layers if l.type_name == "SIGNAL"]
        colours = {layer_colour(l, i) for i, l in enumerate(signals[:4])}
        assert len(colours) > 1

    def test_selection_is_a_different_shade(self):
        _job, preview, _diag = preview_of("minimal-test-odb")
        top = preview.layer("top")
        assert layer_colour(top, 0) != layer_colour(top, 0, selected=True)

    def test_the_ordered_list_puts_substrates_before_annotations(self):
        from odbpp.svg import _Z_RANK, _ordered

        _job, preview, _diag = preview_of("kicad-video-odb.tgz")
        ranks = [_Z_RANK.get(l.type_name, 5) for l in _ordered(preview.layers,
                                                              SvgOptions())]
        assert ranks == sorted(ranks)

    def test_drills_are_opaque_and_dielectrics_are_faded(self):
        # Without the fade the default "everything on" view is dominated by
        # whichever layer covers the most area.
        _job, preview, _diag = preview_of("kicad-video-odb.tgz")
        for layer in preview.layers:
            opacity = layer_opacity(layer)
            if layer.type_name == "DRILL":
                assert opacity == 1.0
            elif layer.type_name == "DIELECTRIC":
                assert opacity < 0.5

    def test_every_layer_group_declares_an_opacity(self):
        _job, preview, _diag = preview_of("minimal-test-odb")
        for group in layer_groups(parse(render_svg(preview))):
            assert group.get("fill-opacity") is not None


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

class TestHtml:
    def test_layer_order_control_preserves_type_default_and_exposes_stackup_rows(self):
        from odbpp.svg import _ordered

        _job, preview, _diag = preview_of("kicad-kitchen-sink")
        text = render_html(preview)
        assert '<option value="type">By type</option>' in text
        assert '<option value="stackup">Top to bottom</option>' in text
        rows = re.findall(
            r'<div class="row" data-name="([^"]*)" '
            r'data-type="[^"]*" data-row="(\d+)"', text)
        assert [name for name, _ in rows] == [
            layer.name for layer in _ordered(preview.layers, SvgOptions())]
        assert {name: int(row) for name, row in rows} == {
            layer.name: layer.layer.ref.row for layer in preview.layers}
        assert "Number(a.dataset.row) - Number(b.dataset.row)" in text

    def test_it_is_a_complete_document(self):
        _job, preview, _diag = preview_of("minimal-test-odb")
        text = render_html(preview)
        assert text.startswith("<!DOCTYPE html>")
        assert text.rstrip().endswith("</html>")

    def test_it_references_no_external_resources(self):
        # The page has to open from a file:// URL and work offline.
        _job, preview, _diag = preview_of("minimal-test-odb")
        text = render_html(preview)
        # The SVG namespace URI is the one http:// string that has to be there;
        # everything else would be a fetch.
        stripped = text.replace("http://www.w3.org/2000/svg", "")
        assert "http://" not in stripped
        assert "https://" not in stripped
        assert "<link" not in stripped
        assert "src=" not in stripped

    def test_the_svg_is_inlined(self):
        _job, preview, _diag = preview_of("minimal-test-odb")
        text = render_html(preview)
        assert '<svg xmlns="http://www.w3.org/2000/svg"' in text
        assert b"<img" not in text.encode()

    def test_there_is_one_row_per_rendered_layer(self):
        _job, preview, _diag = preview_of("minimal-test-odb")
        text = render_html(preview)
        rows = re.findall(r'<div class="row"[^>]*data-name="([^"]*)"', text)
        root = parse(render_svg(preview))
        groups = {g.get("data-name") for g in layer_groups(root)}
        assert set(rows) == groups

    def test_empty_layers_start_unchecked(self):
        _job, preview, _diag = preview_of("fx_negative_layer")
        text = render_html(preview)
        for match in re.finditer(r'<div class="row" data-name="([^"]*)"[^>]*>'
                                 r'(<input type="checkbox"[^>]*>)', text):
            name, checkbox = match.group(1), match.group(2)
            layer = preview.layer(name)
            if layer is not None and layer.is_empty:
                assert "checked" not in checkbox

    def test_it_names_the_step_and_the_input_units(self):
        _job, preview, _diag = preview_of("minimal-test-odb")
        text = render_html(preview)
        assert preview.step.name in text
        assert preview.units_in in text

    def test_the_layer_rows_carry_z_for_the_info_panel(self):
        _job, preview, _diag = preview_of("kicad-kitchen-sink")
        text = render_html(preview)
        assert 'data-z-start="0.010"' in text
        assert 'data-z-end="0.011"' in text
        # The viewer reads the dashes as camelCase; keep them wired up.
        assert "dataset.zStart" in text

    def test_the_header_states_the_board_thickness(self):
        _job, preview, _diag = preview_of("kicad-kitchen-sink")
        assert "mm thick" in render_html(preview)

    def test_a_failed_layer_is_called_out(self):
        diagnostics = Diagnostics()
        job = load_job(str(sample_path("fx_bad_layer_profile")),
                       diagnostics=diagnostics)
        preview = build_step_preview(job, diagnostics=diagnostics)
        text = render_html(preview)
        assert "could not be composed" in text
        assert "DIELECTRIC_1" in text

    def test_the_javascript_does_not_double_flip(self):
        # The flip lives on #odb-flip in the markup.  If the viewer also flipped,
        # the board would render upside down while still looking plausible.
        _job, preview, _diag = preview_of("minimal-test-odb")
        text = render_html(preview)
        script = text[text.index("<script>"):text.index("</script>")]
        assert "scale(1 -1)" not in script

    def test_titles_and_source_are_escaped(self):
        _job, preview, _diag = preview_of("minimal-test-odb")
        text = render_html(preview, title='<script>alert("x")</script>',
                           source="a&b")
        assert "<script>alert" not in text
        assert "a&amp;b" in text


# ---------------------------------------------------------------------------
# writing
# ---------------------------------------------------------------------------

class TestWriteOutputs:
    def test_it_writes_the_three_artefacts(self, tmp_path):
        job, preview, _diag = preview_of("minimal-test-odb")
        outputs = write_outputs(preview, job, str(tmp_path))
        assert outputs.json and outputs.svg and outputs.html
        for path in outputs.all_paths():
            assert (tmp_path / path.split("\\")[-1].split("/")[-1]).exists()

    def test_the_stem_is_the_step_name(self, tmp_path):
        job, preview, _diag = preview_of("minimal-test-odb")
        outputs = write_outputs(preview, job, str(tmp_path))
        assert outputs.json.endswith("pcb.json")

    def test_only_json_skips_the_drawings(self, tmp_path):
        job, preview, _diag = preview_of("minimal-test-odb")
        outputs = write_outputs(preview, job, str(tmp_path),
                                want_svg=False, want_html=False)
        assert outputs.json and outputs.svg is None and outputs.html is None

    def test_per_layer_svg_writes_one_file_each(self, tmp_path):
        job, preview, _diag = preview_of("minimal-test-odb")
        outputs = write_outputs(preview, job, str(tmp_path), per_layer_svg=True,
                                want_json=False, want_svg=False, want_html=False)
        assert len(outputs.layer_svgs) == len(preview.layers)

    def test_layer_names_are_made_filename_safe(self, tmp_path):
        job, preview, _diag = preview_of("fx_negative_layer")
        outputs = write_outputs(preview, job, str(tmp_path), per_layer_svg=True,
                                want_json=False, want_svg=False, want_html=False)
        for path in outputs.layer_svgs:
            name = path.replace("\\", "/").split("/")[-1][:-4]
            assert all(c.isalnum() or c in "-_." for c in name)

    def test_the_rewritten_files_are_valid(self, tmp_path):
        job, preview, _diag = preview_of("minimal-test-odb")
        outputs = write_outputs(preview, job, str(tmp_path))
        with open(outputs.svg, encoding="utf-8") as handle:
            parse(handle.read())
        import json

        with open(outputs.json, encoding="utf-8") as handle:
            document = json.load(handle)
        assert "schema" not in document


# ---------------------------------------------------------------------------
# one geometry per run
# ---------------------------------------------------------------------------

class TestOutputPrecisionConsistency:
    """The JSON, the SVG and the HTML must describe the same polygons.

    The regression these cover printed live coordinates at a coarser precision
    than the model: a 0.00002 mm-wide pad became ``M0 0L0 0L0 1L0 1Z`` in the
    drawing while the JSON beside it still reported its area.
    """

    @staticmethod
    def _six_digit_preview():
        from odbpp.clipper import Region, Ring, to_surface_geometry
        from odbpp.export import PreviewOptions
        from odbpp.features import SurfaceFeature

        job = load_job(str(sample_path("minimal-test-odb")))
        layer = job.single_step.layers[0]
        narrow = Ring(((0, 0), (0, 1), (0.00002, 1), (0.00002, 0)), False)
        layer.features.records = [SurfaceFeature(
            line_no=1, geometry=to_surface_geometry(Region([narrow])))]
        preview = build_step_preview(job, options=PreviewOptions(
            layers=[layer.name], round_digits=6))
        return job, layer.name, preview

    def test_the_finer_preview_precision_reaches_the_drawing_and_the_html(self, tmp_path):
        import json
        from pathlib import Path

        job, name, preview = self._six_digit_preview()
        outputs = write_outputs(preview, job, str(tmp_path))
        document = json.loads(Path(outputs.json).read_text(encoding="utf-8"))
        points = document["steps"][0]["layers"][0]["polygons"][0]["points"]
        assert any(abs(x - 0.00002) < 1e-9 for x, _y in points)

        root = parse(Path(outputs.svg).read_text(encoding="utf-8"))
        group = next(g for g in layer_groups(root) if g.get("data-name") == name)
        path = path_of(group)
        assert "0.00002" in path, path
        assert path in Path(outputs.html).read_text(encoding="utf-8")

    def test_an_explicit_coarser_drawing_precision_re_regularises(self, tmp_path):
        # Only changing the default would not be enough: an explicit request
        # still has to re-quantise the region rather than round its points.
        import json
        from pathlib import Path

        job, name, preview = self._six_digit_preview()
        outputs = write_outputs(preview, job, str(tmp_path),
                                svg_options=SvgOptions(round_digits=2))
        document = json.loads(Path(outputs.json).read_text(encoding="utf-8"))
        assert document["steps"][0]["layers"][0]["polygons"], \
            "the JSON still follows the preview's 6-digit resolution"

        root = parse(Path(outputs.svg).read_text(encoding="utf-8"))
        group = next(g for g in layer_groups(root) if g.get("data-name") == name)
        assert group.get("data-rings") == "0"
        assert path_of(group) == ""
        assert float(group.get("data-area")) == 0.0

    def test_an_explicit_coarser_json_precision_agrees_with_the_drawing(self, tmp_path):
        # A caller that coarsens both must get one collapsed geometry in both
        # artefacts, not a collapsed drawing next to uncollapsed polygons.
        import json
        from pathlib import Path

        from odbpp.export import PreviewOptions

        job, name, preview = self._six_digit_preview()
        outputs = write_outputs(preview, job, str(tmp_path),
                                preview_options=PreviewOptions(round_digits=4))
        document = json.loads(Path(outputs.json).read_text(encoding="utf-8"))
        layer = document["steps"][0]["layers"][0]
        assert layer["polygons"] == []
        assert layer["geometry_status"] == "ready" and layer["polygons"] == []
        root = parse(Path(outputs.svg).read_text(encoding="utf-8"))
        group = next(g for g in layer_groups(root) if g.get("data-name") == name)
        assert path_of(group) == ""
        assert group.get("data-rings") == "0"
