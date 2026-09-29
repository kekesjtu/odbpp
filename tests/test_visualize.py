"""Visualisation: the PNG encoder, material masks and kx/ky/kz heat maps.

The images are the only deliverables of the project a person looks at directly,
so they are tested by decoding them again rather than by trusting that a file
appeared.  The decoder here is written to the subset the encoder emits (8-bit
greyscale/RGB, filter type 0), which is also a check that the encoder stays in
that subset.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest

from odbpp.diagnostics import Diagnostics
from odbpp.equiv import EquivalenceOptions, equivalence_from_document, write_materialmap
from odbpp.grid import GridOptions
from odbpp.materials import MaterialBook
from odbpp.png import (
    FONT_HEIGHT,
    Canvas,
    draw_text,
    text_width,
    write_greyscale_png,
    write_rgb_png,
)
from odbpp.raster import LayerMask, RasterAxis
from odbpp.rectify import RectifyOptions
from odbpp.visualize import (
    NO_DATA_COLOUR,
    HeatmapOptions,
    VisualizeError,
    auto_mask_scale,
    gallery_entries,
    mask_image_size,
    read_materialmap,
    render_heatmaps,
    safe_name,
    write_gallery_html,
    write_layer_mask,
)

from conftest import case_path


# ---------------------------------------------------------------------------
# decoding helpers
# ---------------------------------------------------------------------------

def decode_png(path) -> tuple[int, int, int, list[bytes]]:
    """Decode a PNG written by :mod:`odbpp.png` into ``(w, h, colour, rows)``."""
    data = Path(path).read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "missing PNG signature"
    position = 8
    header = None
    payload = b""
    while position < len(data):
        length = struct.unpack(">I", data[position:position + 4])[0]
        tag = data[position + 4:position + 8]
        body = data[position + 8:position + 8 + length]
        if tag == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif tag == b"IDAT":
            payload += body
        elif tag == b"IEND":
            break
        position += 12 + length
    assert header is not None, "no IHDR chunk"
    width, height, depth, colour, compression, filter_method, interlace = header
    assert (depth, compression, filter_method, interlace) == (8, 0, 0, 0)
    raw = zlib.decompress(payload)
    stride = 1 + width * (3 if colour == 2 else 1)
    assert len(raw) == height * stride, "IDAT does not hold exactly one filtered row per line"
    rows = []
    for y in range(height):
        line = raw[y * stride:(y + 1) * stride]
        assert line[0] == 0, "the encoder must only emit filter type 0"
        rows.append(line[1:])
    return width, height, colour, rows


def grey(rows: list[bytes], x: int, y: int) -> int:
    return rows[y][x]


def rgb(rows: list[bytes], x: int, y: int) -> tuple[int, int, int]:
    return tuple(rows[y][3 * x:3 * x + 3])  # type: ignore[return-value]


def colour_counts(rows: list[bytes], colour: tuple[int, int, int]) -> int:
    target = bytes(colour)
    return sum(row[3 * x:3 * x + 3] == target
               for row in rows for x in range(len(row) // 3))


# ---------------------------------------------------------------------------
# synthetic preview documents (the equivalence input contract)
# ---------------------------------------------------------------------------

def rect(x0: float, y0: float, x1: float, y1: float) -> dict:
    return {"island": True,
            "points": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]}


def hole(x0: float, y0: float, x1: float, y1: float) -> dict:
    return {"island": False,
            "points": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]}


def document(layers, *, profile=None, domains=None) -> dict:
    profile = profile if profile is not None else rect(0, 0, 10, 10)
    domain_list = domains or [{"id": "step_profile", "source": "step_profile",
                               "layer": None, "polygons": [profile]}]
    layer_docs = []
    stackup = []
    for spec in layers:
        extent = spec.get("z_mm") or {
            "start_mm": 0.0, "end_mm": 0.035, "thickness_known": True}
        layer_docs.append({"name": spec["name"],
                           "type": spec.get("type", "SIGNAL"),
                           "polygons": spec.get("polygons", []),
                           "domain_id": spec.get("domain_id", "step_profile"),
                           "z_mm": extent})
        stackup.append({"name": spec["name"], "type": spec.get("type", "SIGNAL"),
                        "z_mm": extent})
    return {"schema": "odbpp/preview-2", "units": "mm",
            "steps": [{"name": "pcb", "domains": domain_list,
                       "layers": layer_docs, "stackup": stackup}]}


def run(document_dict, *, px=4, py=4, nx=None, ny=None, mask_sink=None):
    options = EquivalenceOptions(
        grid=GridOptions(px=px, py=py, nx=nx, ny=ny),
        materials=MaterialBook(),
        rectify=RectifyOptions(tolerance_mm=0.01),
        warn_partial_domains=False,
    )
    return equivalence_from_document(document_dict, options,
                                     diagnostics=Diagnostics(),
                                     mask_sink=mask_sink)


# ---------------------------------------------------------------------------
# PNG encoder and canvas
# ---------------------------------------------------------------------------

class TestPngWriter:
    def test_greyscale_rows_round_trip(self, tmp_path):
        path = tmp_path / "grey.png"
        write_greyscale_png(str(path), 3, 2,
                            [b"\x00\x10\x20", b"\x30\x40\x50"])
        width, height, colour, rows = decode_png(path)
        assert (width, height, colour) == (3, 2, 0)
        assert rows == [b"\x00\x10\x20", b"\x30\x40\x50"]

    def test_rgb_rows_round_trip(self, tmp_path):
        path = tmp_path / "rgb.png"
        write_rgb_png(str(path), 2, 1, [bytes((1, 2, 3, 4, 5, 6))])
        width, height, colour, rows = decode_png(path)
        assert (width, height, colour) == (2, 1, 2)
        assert rgb(rows, 0, 0) == (1, 2, 3)
        assert rgb(rows, 1, 0) == (4, 5, 6)

    def test_a_row_of_the_wrong_length_is_refused(self, tmp_path):
        with pytest.raises(ValueError):
            write_greyscale_png(str(tmp_path / "bad.png"), 3, 2, [b"\x00\x10"])

    def test_a_wrong_row_count_is_refused(self, tmp_path):
        with pytest.raises(ValueError):
            write_greyscale_png(str(tmp_path / "bad.png"), 2, 3, [b"\x00\x10"])

    def test_zero_sized_images_are_refused(self, tmp_path):
        with pytest.raises(ValueError):
            write_rgb_png(str(tmp_path / "bad.png"), 0, 1, [])


class TestCanvas:
    def test_rect_and_blit_row_compose_the_expected_pixels(self, tmp_path):
        canvas = Canvas(4, 2, (255, 255, 255))
        canvas.rect(1, 0, 3, 1, (10, 20, 30))
        canvas.blit_row(1, 2, bytes((1, 2, 3, 4, 5, 6)))
        path = tmp_path / "canvas.png"
        canvas.save(str(path))
        _, _, colour, rows = decode_png(path)
        assert colour == 2
        assert [rgb(rows, x, 0) for x in range(4)] == [
            (255, 255, 255), (10, 20, 30), (10, 20, 30), (255, 255, 255)]
        assert [rgb(rows, x, 1) for x in range(4)] == [
            (255, 255, 255), (255, 255, 255), (1, 2, 3), (4, 5, 6)]

    def test_frame_is_one_pixel_and_rect_is_clipped(self, tmp_path):
        canvas = Canvas(5, 4, (0, 0, 0))
        canvas.frame(1, 1, 4, 3, (9, 9, 9))
        canvas.rect(-5, -5, 100, 100, (7, 7, 7))  # fully outside except clipped
        path = tmp_path / "frame.png"
        canvas.save(str(path))
        _, _, _, rows = decode_png(path)
        assert rgb(rows, 1, 1) == (7, 7, 7)
        assert rgb(rows, 0, 0) == (7, 7, 7)  # the big rect overrode everything

    def test_text_draws_ink_and_unknown_characters_stay_visible(self, tmp_path):
        canvas = Canvas(text_width("12.3", 2) + 8, FONT_HEIGHT * 2 + 4,
                        (255, 255, 255))
        draw_text(canvas, 2, 2, "12.3", (0, 0, 0), 2)
        draw_text(canvas, 2, FONT_HEIGHT * 2 + 1, "\u4e2d", (0, 0, 0), 1)
        path = tmp_path / "text.png"
        canvas.save(str(path))
        _, _, _, rows = decode_png(path)
        assert colour_counts(rows, (0, 0, 0)) > 40
        # The tofu box for an unsupported character is a hollow rectangle.
        assert rgb(rows, 3, FONT_HEIGHT * 2 + 1) == (0, 0, 0)

    def test_text_width_accounts_for_the_trailing_gap(self):
        assert text_width("", 2) == 0
        assert text_width("1", 1) == 5
        assert text_width("12", 2) == 2 * 6 * 2 - 2


# ---------------------------------------------------------------------------
# layer masks
# ---------------------------------------------------------------------------

class TestLayerMask:
    def test_spans_fill_tones_per_row(self):
        axis = RasterAxis(origin=0.0, step=1.0, count=3)
        mask = LayerMask(3, 2)
        mask.set_spans(0, [(0.0, 2.0)], [(0.0, 1.0)], axis)
        assert list(mask.pixels[:3]) == [LayerMask.BLACK, LayerMask.WHITE,
                                         LayerMask.OUTSIDE]
        assert list(mask.pixels[3:]) == [LayerMask.OUTSIDE] * 3
        assert mask.tone_counts() == {"black": 1, "white": 1, "outside": 4}

    def test_black_wins_over_white_inside_the_domain(self):
        axis = RasterAxis(origin=0.0, step=1.0, count=2)
        mask = LayerMask(2, 1)
        mask.set_spans(0, [(0.0, 2.0)], [(0.0, 2.0)], axis)
        assert list(mask.pixels) == [LayerMask.BLACK] * 2

    def test_top_down_rows_flip_and_replicate(self):
        mask = LayerMask(2, 2)
        mask.pixels[0:2] = bytes([0, 0])        # bottom row
        mask.pixels[2:4] = bytes([255, 255])    # top row
        assert list(mask.top_down_rows(1)) == [b"\xff\xff", b"\x00\x00"]
        assert list(mask.top_down_rows(2)) == [b"\xff\xff\xff\xff"] * 2 + \
            [b"\x00\x00\x00\x00"] * 2

    def test_a_bad_size_or_scale_is_refused(self):
        with pytest.raises(ValueError):
            LayerMask(0, 1)
        with pytest.raises(ValueError):
            list(LayerMask(1, 1).top_down_rows(0))
        with pytest.raises(ValueError):
            list(LayerMask(1, 1).resampled_rows(0, 2))

    def test_resampled_rows_stretch_the_axes_independently(self):
        mask = LayerMask(2, 2)
        mask.pixels[0:2] = bytes([LayerMask.BLACK, LayerMask.WHITE])   # bottom
        mask.pixels[2:4] = bytes([LayerMask.WHITE, LayerMask.BLACK])   # top
        # Top row first, X doubled, Y left alone: the board proportions, not a
        # uniform zoom.
        assert list(mask.resampled_rows(4, 2)) == [
            bytes([LayerMask.WHITE, LayerMask.WHITE, LayerMask.BLACK, LayerMask.BLACK]),
            bytes([LayerMask.BLACK, LayerMask.BLACK, LayerMask.WHITE, LayerMask.WHITE]),
        ]

    def test_resampled_rows_refuses_an_empty_picture(self):
        with pytest.raises(ValueError):
            list(LayerMask(2, 2).resampled_rows(2, 0))

    def test_auto_scale_never_shrinks_and_targets_a_viewable_size(self):
        assert auto_mask_scale(LayerMask(10, 10), target=700) == 70
        assert auto_mask_scale(LayerMask(800, 400), target=700) == 1


class TestMaskFromEquivalence:
    def test_the_mask_is_the_raster_the_formulas_integrated(self, tmp_path):
        captured: list[tuple[str, LayerMask, tuple[float, float]]] = []
        result = run(document([{"name": "CU", "polygons": [rect(0, 0, 10, 10)]}],
                              profile=rect(0, 0, 10, 5)),
                     px=4, py=4,
                     mask_sink=lambda name, mask, step: captured.append(
                         (name, mask, step)))
        assert not result.fatal
        assert [name for name, _, _ in captured] == ["CU"]
        name, mask, step = captured[0]
        assert (mask.px, mask.py) == (4, 4)
        # The physical size of one raster pixel travels with the mask, because
        # a picture that keeps the board's proportions needs both.
        assert step == pytest.approx((2.5, 1.25))
        rows = list(mask.top_down_rows())
        # Board Y up: the copper fills the board, so every pixel is black.
        assert rows == [bytes([LayerMask.BLACK]) * 4] * 4

    def test_board_outside_the_domain_stays_grey_not_white(self):
        domains = [
            {"id": "step_profile", "source": "step_profile", "layer": None,
             "polygons": [rect(0, 0, 10, 10)]},
            {"id": "left", "source": "layer_profile", "layer": "CU",
             "polygons": [rect(0, 0, 5, 10)]},
        ]
        captured: list[LayerMask] = []
        run(document([{"name": "CU", "polygons": [rect(0, 0, 10, 10)],
                       "domain_id": "left"}], domains=domains),
            px=4, py=4, mask_sink=lambda name, mask, step: captured.append(mask))
        mask = captured[0]
        # Left half in the domain (and copper), right half has no domain at all.
        rows = list(mask.top_down_rows())
        assert list(rows[0]) == [
            LayerMask.BLACK, LayerMask.BLACK, LayerMask.OUTSIDE, LayerMask.OUTSIDE]
        assert mask.tone_counts() == {"black": 8, "white": 0, "outside": 8}

    def test_only_equivalented_layers_get_a_mask(self):
        captured: list[str] = []
        run(document([
            {"name": "CU", "polygons": [rect(0, 0, 10, 10)]},
            {"name": "DOC", "type": "DOCUMENT", "polygons": [rect(0, 0, 1, 1)]},
        ]), mask_sink=lambda name, mask, step: captured.append(name))
        assert captured == ["CU"]

    def test_mask_png_has_the_expected_size_and_tones(self, tmp_path):
        class Holder:
            mask: LayerMask

        holder = Holder()
        run(document([{"name": "CU", "polygons": [rect(0, 0, 10, 5)]}]),
            px=4, py=4,
            mask_sink=lambda name, mask, step: setattr(holder, "mask", mask))
        image = write_layer_mask(holder.mask, str(tmp_path / "m.png"), scale=2)
        assert (image.width, image.height) == (8, 8)
        assert image.isotropic_zoom == 2.0
        width, height, colour, rows = decode_png(tmp_path / "m.png")
        assert (width, height, colour) == (8, 8, 0)
        assert grey(rows, 0, 0) == LayerMask.WHITE
        assert grey(rows, 0, height - 1) == LayerMask.BLACK

    def test_the_picture_keeps_the_board_proportions(self, tmp_path):
        # The raster counts 4 pixels along a 10 mm side and 4 along a 5 mm one,
        # so one raster pixel is 2.5 mm wide and 1.25 mm tall.  Drawing both axes
        # at the same zoom would show the board as a square.
        class Holder:
            mask: LayerMask
            step: tuple[float, float] = (0.0, 0.0)

        holder = Holder()
        run(document([{"name": "CU", "polygons": [rect(0, 0, 10, 10)]}],
                     profile=rect(0, 0, 10, 5)),
            px=4, py=4,
            mask_sink=lambda name, mask, step: (
                setattr(holder, "mask", mask), setattr(holder, "step", step)))
        image = write_layer_mask(holder.mask, str(tmp_path / "m.png"),
                                 pixel_step_mm=holder.step)
        width, height, _, _ = decode_png(tmp_path / "m.png")
        assert (width, height) == (image.width, image.height)
        assert width / height == pytest.approx(10 / 5, rel=0.02)
        # One zoom per axis: the raster pixels are not square, but the output
        # pixels are, and both axes end up at the same pixels per millimetre.
        assert image.zoom_x / image.zoom_y == pytest.approx(2.5 / 1.25, rel=0.02)
        # Same pixels per millimetre on both axes -- that is what keeps the
        # proportions, so the zoom over a raster pixel differs by the pitch ratio.
        assert (image.zoom_x / holder.step[0]
                == pytest.approx(image.zoom_y / holder.step[1], rel=0.02))
        assert image.isotropic_zoom is None

    def test_one_output_pixel_per_raster_pixel_is_never_undersampled(self):
        # A fine raster on a narrow board: the proportions are kept without
        # ever putting two raster pixels on one output pixel.
        mask = LayerMask(400, 400)
        width, height = mask_image_size(mask, (0.4084, 0.1082))
        assert width >= mask.px and height >= mask.py
        assert width / height == pytest.approx(163.36 / 43.28, rel=0.01)


# ---------------------------------------------------------------------------
# reading the written CSV
# ---------------------------------------------------------------------------

class TestReadMaterialMap:
    def _csv(self, tmp_path, **kwargs) -> Path:
        result = run(document([
            {"name": "CU", "polygons": [rect(0, 0, 10, 5)]},
            {"name": "FR", "type": "DIELECTRIC", "polygons": []},
        ]), **kwargs)
        path = tmp_path / "pcb.MaterialMap.csv"
        write_materialmap(result, str(path))
        return path

    def test_reads_back_the_grid_and_every_layer(self, tmp_path):
        path = self._csv(tmp_path, px=4, py=4)
        data = read_materialmap(str(path))
        assert [series.name for series in data.series] == ["CU", "FR"]
        assert data.row_count == 32
        assert data.skipped_invalid == 0
        assert data.column_count == 4 and data.row_band_count == 4
        assert data.x_edges == (0.0, 2.5, 5.0, 7.5, 10.0)
        assert data.bounds_mm() == (0.0, 0.0, 10.0, 10.0)
        assert len(data.series[0]) == 16
        assert data.x_interval_of_column[3] == 3
        assert data.y_interval_of_row[3] == 3

    def test_a_foreign_csv_is_refused_with_the_missing_columns(self, tmp_path):
        path = tmp_path / "other.csv"
        path.write_text("a,b\n1,2\n", encoding="utf-8")
        with pytest.raises(VisualizeError) as excinfo:
            read_materialmap(str(path))
        assert "layer" in str(excinfo.value)

    def test_an_unreadable_file_is_refused(self, tmp_path):
        with pytest.raises(VisualizeError):
            read_materialmap(str(tmp_path / "nope.csv"))

    def test_unusable_values_are_counted_not_drawn(self, tmp_path):
        path = self._csv(tmp_path, px=4, py=4)
        lines = path.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines[1:], start=1):
            if ",0.3," in line:  # a base-laminate cell on the copper layer
                lines[index] = line.replace(",0.3,", ",nan,", 1)
                break
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        data = read_materialmap(str(path))
        assert data.skipped_invalid == 1
        assert sum(len(series) for series in data.series) == 31

    def test_an_incoherent_grid_is_refused(self, tmp_path):
        path = tmp_path / "broken.csv"
        header = ("layer,layer_type,cell_i,cell_j,cell_x0_mm,cell_y0_mm,"
                  "cell_x1_mm,cell_y1_mm,z_start_mm,z_end_mm,z_thickness_known,"
                  "kx_w_mk,ky_w_mk,kz_w_mk\n")
        rows = (
            "CU,SIGNAL,0,0,0.0,0.0,1.0,1.0,0.0,0.035,1,1.0,1.0,1.0\n"
            "CU,SIGNAL,0,1,0.5,1.0,1.0,2.0,0.0,0.035,1,1.0,1.0,1.0\n"
        )
        path.write_text(header + rows, encoding="utf-8")
        with pytest.raises(VisualizeError) as excinfo:
            read_materialmap(str(path))
        assert "rectilinear" in str(excinfo.value)


# ---------------------------------------------------------------------------
# heat maps
# ---------------------------------------------------------------------------

def frames(rows: list[bytes], colour: tuple[int, int, int]):
    """Contiguous row and column runs that contain *colour*."""
    target = bytes(colour)
    height = len(rows)
    width = len(rows[0]) // 3
    columns = [x for x in range(width)
               if any(rows[y][3 * x:3 * x + 3] == target for y in range(height))]
    lined = [y for y in range(height)
             if any(rows[y][3 * x:3 * x + 3] == target for x in range(width))]
    return _runs(columns), _runs(lined)


def _runs(values: list[int]) -> list[tuple[int, int]]:
    out: list[list[int]] = []
    for value in values:
        if out and value == out[-1][1] + 1:
            out[-1][1] = value
        else:
            out.append([value, value])
    return [(a, b) for a, b in out]


def panels(rows: list[bytes], frame_colour: tuple[int, int, int]):
    """The three panel bodies and the three colour bars, as bounding boxes.

    The panel bodies are located *by their frame*, so the test does not have to
    repeat the layout arithmetic that produced the image.  The tallest framed
    band is the panel body; the last one is its colour bar (the legend swatch in
    the header is framed too, which is why the body is found by size).
    """
    columns, lines = frames(rows, frame_colour)
    assert len(columns) == 3, "kx, ky and kz are three panels side by side"
    assert len(lines) >= 2, "each panel needs a body and a colour bar"
    body_top, body_bottom = max(lines, key=lambda run: run[1] - run[0])
    bar_top, bar_bottom = max(lines, key=lambda run: run[0])
    assert (bar_top, bar_bottom) != (body_top, body_bottom)
    boxes = []
    for x0, x1 in columns:
        boxes.append(((x0 + 1, body_top + 1, x1 - 1, body_bottom - 1),
                      (x0 + 1, bar_top + 1, x1 - 1, bar_bottom - 1)))
    return boxes


def sample(box, u: float, v: float, rows: list[bytes]):
    """Colour at the relative position ``(u, v)`` inside a bounding box."""
    x0, y0, x1, y1 = box
    x = x0 + min(max(int(u * (x1 - x0)), 0), x1 - x0 - 1)
    y = y0 + min(max(int(v * (y1 - y0)), 0), y1 - y0 - 1)
    return rgb(rows, x, y)


RAMP_LOW = (12, 12, 64)
RAMP_HIGH = (176, 16, 32)


def near(actual, expected, tolerance: int = 16) -> bool:
    """True when two colours agree within the first/last bar pixel's drift."""
    return all(abs(a - b) <= tolerance for a, b in zip(actual, expected))


class TestHeatmapRendering:
    def _report(self, tmp_path, document_dict, *, px=4, py=4, out="maps",
                layers=None, **kwargs):
        csv_path = tmp_path / "pcb.MaterialMap.csv"
        write_materialmap(run(document_dict, px=px, py=py), str(csv_path))
        options = HeatmapOptions(panel_width_px=200, font_scale=1, **kwargs)
        return render_heatmaps(str(csv_path), str(tmp_path / out),
                               options=options, layers=layers)

    def test_one_image_per_layer_with_three_directions(self, tmp_path):
        report = self._report(tmp_path, document([
            {"name": "CU", "polygons": [rect(0, 0, 10, 10)]},
            {"name": "FR", "type": "DIELECTRIC", "polygons": []},
        ]))
        assert [image.layer for image in report.images] == ["CU", "FR"]
        assert [image.cells for image in report.images] == [16, 16]
        assert len(list(Path(report.out_dir).glob("*.png"))) == 2
        width, height, colour, rows = decode_png(report.images[0].path)
        assert colour == 2
        boxes = panels(rows, HeatmapOptions().frame_colour)
        assert len(boxes) == 3, "kx, ky and kz must be three panels side by side"
        # The colour bar spans the layer's range from end to end.
        for _, bar in boxes:
            assert near(sample(bar, 0.0, 0.5, rows), RAMP_LOW)
            assert near(sample(bar, 1.0, 0.5, rows), RAMP_HIGH)

    def test_values_map_to_the_ramp_in_board_order(self, tmp_path):
        # Copper on the top half: the top row of cells is the ramp maximum, the
        # bottom row the minimum, and the picture is not upside down.
        report = self._report(tmp_path, document([
            {"name": "HALF", "polygons": [rect(0, 5, 10, 10)]}]))
        rows = decode_png(report.images[0].path)[3]
        boxes = panels(rows, HeatmapOptions().frame_colour)
        for body, _ in boxes:
            assert near(sample(body, 0.5, 0.01, rows), RAMP_HIGH)
            assert near(sample(body, 0.5, 0.99, rows), RAMP_LOW)

    def test_a_uniform_layer_sits_in_the_middle_of_its_range(self, tmp_path):
        report = self._report(tmp_path, document([
            {"name": "CU", "polygons": [rect(0, 0, 10, 10)]}]))
        image = report.images[0]
        assert image.vmin == image.vmax == pytest.approx(385.0)
        rows = decode_png(image.path)[3]
        for body, _ in panels(rows, HeatmapOptions().frame_colour):
            assert not near(sample(body, 0.5, 0.5, rows), RAMP_LOW)
            assert not near(sample(body, 0.5, 0.5, rows), RAMP_HIGH)

    def test_cells_the_layer_does_not_cover_are_left_blank(self, tmp_path):
        # The panel spans the grid of the file, so a cell that *some* layer
        # covers but this one does not is drawn as "no data" rather than
        # squeezed away or painted as base laminate.
        domains = [
            {"id": "step_profile", "source": "step_profile", "layer": None,
             "polygons": [rect(0, 0, 10, 10)]},
            {"id": "left", "source": "layer_profile", "layer": "CU",
             "polygons": [rect(0, 0, 5, 10)]},
        ]
        report = self._report(
            tmp_path,
            document([{"name": "CU", "polygons": [rect(0, 0, 10, 10)],
                       "domain_id": "left"},
                      {"name": "ALL", "polygons": [],
                       "domain_id": "step_profile"}], domains=domains),
            layers=["CU"])
        assert [image.layer for image in report.images] == ["CU"]
        rows = decode_png(report.images[0].path)[3]
        for body, _ in panels(rows, HeatmapOptions().frame_colour):
            # The left half is the layer, the right half has no domain at all.
            assert sample(body, 0.25, 0.5, rows) != NO_DATA_COLOUR
            assert sample(body, 0.75, 0.5, rows) == NO_DATA_COLOUR

    def test_a_shared_scale_uses_one_ramp_for_every_layer(self, tmp_path):
        document_dict = document([
            {"name": "CU", "polygons": [rect(0, 0, 10, 10)]},
            {"name": "HALF", "polygons": [rect(0, 5, 10, 10)]},
        ])
        alone = self._report(tmp_path, document_dict, out="per-layer")
        shared = self._report(tmp_path, document_dict, out="shared",
                              shared_scale=True)
        assert alone.images[0].vmin == alone.images[0].vmax
        assert shared.images[0].vmin == shared.images[1].vmin
        assert shared.images[0].vmax == pytest.approx(385.0)
        assert shared.images[0].vmax == shared.images[1].vmax
        # On its own the all-copper layer is mid-ramp; on the shared scale it is
        # the maximum, which is the whole point of the option.
        alone_rows = decode_png(alone.images[0].path)[3]
        shared_rows = decode_png(shared.images[0].path)[3]
        frame = HeatmapOptions().frame_colour
        assert not near(sample(panels(alone_rows, frame)[0][0], 0.5, 0.5,
                               alone_rows), RAMP_HIGH)
        assert near(sample(panels(shared_rows, frame)[0][0], 0.5, 0.5,
                           shared_rows), RAMP_HIGH)

    def test_the_layer_filter_selects_and_validates(self, tmp_path):
        csv_path = tmp_path / "pcb.MaterialMap.csv"
        write_materialmap(run(document([
            {"name": "CU", "polygons": [rect(0, 0, 10, 10)]},
            {"name": "FR", "type": "DIELECTRIC", "polygons": []},
        ])), str(csv_path))
        report = render_heatmaps(str(csv_path), str(tmp_path / "one"),
                                 layers=["FR"])
        assert [image.layer for image in report.images] == ["FR"]
        assert report.images[0].index == 1 and report.images[0].count == 1
        with pytest.raises(VisualizeError):
            render_heatmaps(str(csv_path), str(tmp_path / "bad"),
                            layers=["NOPE"])

    def test_a_colliding_file_name_does_not_overwrite_a_layer(self, tmp_path):
        csv_path = tmp_path / "pcb.MaterialMap.csv"
        write_materialmap(run(document([
            {"name": "A/B", "polygons": [rect(0, 0, 10, 10)]},
            {"name": "A:B", "polygons": [rect(0, 0, 10, 10)]},
            {"name": "A_B-2", "polygons": [rect(0, 0, 10, 10)]},
        ])), str(csv_path))
        report = render_heatmaps(str(csv_path), str(tmp_path / "clash"),
                                 options=HeatmapOptions(panel_width_px=120,
                                                        font_scale=1))
        assert {Path(image.path).name for image in report.images} == {
            "A_B.png", "A_B-2.png", "A_B-2-2.png"}
        assert all(Path(image.path).is_file() for image in report.images)

    def test_safe_name_keeps_a_usable_stem(self):
        assert safe_name("F.CU") == "F.CU"
        assert safe_name("a/b\\c") == "a_b_c"
        assert safe_name("   ") == "_"
        assert safe_name("CON") == "_CON"


class TestGallery:
    def test_the_gallery_names_what_the_pictures_cannot(self, tmp_path):
        csv_path = tmp_path / "pcb.MaterialMap.csv"
        write_materialmap(run(document([
            {"name": "CU", "polygons": [rect(0, 0, 10, 10)]}])), str(csv_path))
        report = render_heatmaps(str(csv_path), str(tmp_path / "heatmaps"),
                                 options=HeatmapOptions(panel_width_px=120,
                                                        font_scale=1))
        masks = {"CU": "masks/CU.png"}
        path = write_gallery_html(
            str(tmp_path / "pcb.images.html"), "pcb 图例",
            gallery_entries(report, masks=masks, gallery_dir=str(tmp_path)),
            note="drill_model=solid_copper")
        text = Path(path).read_text(encoding="utf-8")
        assert "CU" in text
        assert "heatmaps/CU.png" in text
        assert "masks/CU.png" in text
        assert "drill_model=solid_copper" in text
        assert "z " in text


# ---------------------------------------------------------------------------
# command line
# ---------------------------------------------------------------------------

class TestCommandLine:
    def test_colliding_layer_names_keep_all_masks_and_heatmaps(self, tmp_path):
        import json

        from odbpp.cli import main

        names = ["A/B", "A:B", "A_B-2", "Top", "top"]
        preview = tmp_path / "board.json"
        preview.write_text(json.dumps(document([
            {"name": name, "polygons": [rect(2 * index, 0,
                                               2 * (index + 1), 10)]}
            for index, name in enumerate(names)
        ])), encoding="utf-8")
        out = tmp_path / "eq"

        assert main(["equivalent", "--from-json", str(preview),
                     "--px", "10", "--py", "10", "--raster-scale", "1",
                     "-o", str(out)]) == 0

        expected = {"A_B.png", "A_B-2.png", "A_B-2-2.png",
                    "Top.png", "top-2.png"}
        masks = list((out / "masks").glob("*.png"))
        heatmaps = list((out / "heatmaps").glob("*.png"))
        assert {path.name for path in masks} == expected
        assert {path.name for path in heatmaps} == expected
        assert len({path.read_bytes() for path in masks}) == len(names)
        gallery = (out / "pcb.images.html").read_text(encoding="utf-8")
        for filename in expected:
            assert f"masks/{filename}" in gallery
            assert f"heatmaps/{filename}" in gallery
        assert (out / "pcb.MaterialMap.csv").is_file()
        assert (out / "pcb.MaterialMap.meta.json").is_file()
        assert not list(out.rglob("*.tmp"))

    def test_default_material_warnings_are_visible_on_stderr(
            self, tmp_path, capsys):
        from odbpp.cli import main

        assert main(["equivalent", str(case_path("minimal-test-odb")),
                     "--px", "8", "--py", "8", "-o", str(tmp_path),
                     "--no-raster-png", "--no-heatmap", "--no-gallery"]) == 0
        stderr = capsys.readouterr().err
        assert stderr.count("materials.default_used") == 2
        assert "copper phase" in stderr
        assert "base phase" in stderr
        assert (tmp_path / "pcb.MaterialMap.csv").is_file()

    def test_equivalent_writes_masks_heatmaps_and_a_gallery(self, tmp_path, capsys):
        from odbpp.cli import main

        code = main(["equivalent", str(case_path("minimal-test-odb")),
                     "--px", "16", "--py", "16", "-o", str(tmp_path)])
        assert code == 0
        masks = sorted((tmp_path / "masks").glob("*.png"))
        maps = sorted((tmp_path / "heatmaps").glob("*.png"))
        assert masks and maps
        assert len(masks) == len(maps)
        assert len(list(tmp_path.glob("*.images.html"))) == 1
        for path in masks:
            width, height, colour, _ = decode_png(path)
            assert colour == 0
            # 100 x 75 mm of rectified board at 7 px/mm, so the picture keeps the
            # board's proportions instead of forcing a square.
            assert (width, height) == (700, 525)
        for path in maps:
            assert decode_png(path)[2] == 2
        assert "up to 700x525 px" in capsys.readouterr().out
        assert not list(tmp_path.rglob("*.tmp"))

    def test_the_visualisations_can_be_turned_off(self, tmp_path):
        from odbpp.cli import main

        code = main(["equivalent", str(case_path("minimal-test-odb")),
                     "--px", "8", "--py", "8", "-o", str(tmp_path),
                     "--no-raster-png", "--no-heatmap", "--no-gallery"])
        assert code == 0
        assert not (tmp_path / "masks").exists()
        assert not (tmp_path / "heatmaps").exists()
        assert not list(tmp_path.glob("*.images.html"))

    def test_a_fatal_run_leaves_no_mask_behind(self, tmp_path):
        import json

        from odbpp.cli import main

        # A 0.2 mm slot cannot survive a 5 mm tolerance: the rectangularisation
        # is untrustworthy, so nothing at all may be written -- including the
        # masks, which are produced while the layers are being walked.
        domains = [{"id": "step_profile", "source": "step_profile",
                    "layer": None,
                    "polygons": [rect(0, 0, 10, 10), hole(4.9, 0, 5.1, 10)]}]
        preview = tmp_path / "board.json"
        preview.write_text(json.dumps(document(
            [{"name": "CU", "polygons": []}], domains=domains)),
            encoding="utf-8")
        out = tmp_path / "eq"
        code = main(["equivalent", "--from-json", str(preview),
                     "--px", "20", "--py", "20", "--rectify-tolerance", "5.0",
                     "-o", str(out)])
        assert code == 2
        assert not list(out.rglob("*.png"))
        assert not list(out.rglob("*.tmp"))
        assert not list(out.glob("*.csv"))

    def test_the_heatmap_command_reads_only_the_csv(self, tmp_path):
        from odbpp.cli import main

        assert main(["equivalent", str(case_path("minimal-test-odb")),
                     "--px", "10", "--py", "10", "-o", str(tmp_path / "eq"),
                     "--no-raster-png", "--no-gallery"]) == 0
        csv_path = tmp_path / "eq" / "pcb.MaterialMap.csv"
        out = tmp_path / "from-csv"
        code = main(["heatmap", str(csv_path), "-o", str(out),
                     "--panel-width", "160"])
        assert code == 0
        images = sorted(out.glob("*.png"))
        assert images
        assert (out / "index.html").is_file()
        text = (out / "index.html").read_text(encoding="utf-8")
        assert "top" in text

    def test_the_heatmap_command_refuses_a_missing_file(self, tmp_path):
        from odbpp.cli import main

        assert main(["heatmap", str(tmp_path / "nope.csv")]) == 2

    def test_the_heatmap_command_defaults_next_to_the_csv(self, tmp_path):
        from odbpp.cli import main

        assert main(["equivalent", str(case_path("minimal-test-odb")),
                     "--px", "8", "--py", "8", "-o", str(tmp_path / "eq")]) == 0
        code = main(["heatmap", str(tmp_path / "eq" / "pcb.MaterialMap.csv"),
                     "--panel-width", "120"])
        assert code == 0
        assert (tmp_path / "eq" / "heatmaps" / "index.html").is_file()
