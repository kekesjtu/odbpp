"""SVG and a self-contained HTML viewer (data-flow step 3).

The output is not a picture of the files -- it is the composed geometry from
:mod:`odbpp.export`, drawn.  That distinction is the whole point of the pipeline:
a preview built from raw records would show clearances as solid copper and hide
every negative feature, so a wrong answer would look like a plausible board.

Rendering choices worth stating
-------------------------------
**``fill-rule="nonzero"``, never ``evenodd``.**  The regions are already fully
positive, with islands and holes wound to the spec convention (p.39).  Under
``nonzero`` two overlapping islands merge, which is what a union means.
``evenodd`` would punch a hole at every overlap -- visually convincing and
geometrically wrong.  This is exactly why the earlier idea of approximating the
composition with a single even-odd path was dropped.

**One ``<path>`` per layer, not per ring.**  A layer's rings are concatenated
into a single ``d`` attribute (each ring starts with its own ``M``).  For
``designodb-rigidflex`` that is the difference between one element and 29,235.

**Y is flipped by a transform, not by negating coordinates.**  SVG y grows
downwards and ODB++ y grows upwards.  Keeping the path data in model millimetres
means the numbers in the file can be checked against the JSON, and the flip is a
single ``scale(1,-1)`` that also flips the winding sign -- which is harmless
because ``nonzero`` counts ``-1`` as filled just as it counts ``+1``.

**No external resources.**  The HTML page inlines its CSS, its JavaScript and its
SVG, so it opens from a file:// URL, works offline, and can be emailed to
whoever needs to check the board.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Optional, Sequence

from .clipper import Ring
from .export import (
    LayerPreview,
    PreviewOptions,
    StepPreview,
    _preview_digits,
    regularise_preview,
)

__all__ = [
    "SvgOptions",
    "layer_colour",
    "layer_opacity",
    "path_data",
    "render_svg",
    "render_html",
    "write_outputs",
]


@dataclass(frozen=True)
class SvgOptions:
    """Presentation knobs.

    Colour, background and empty-layer handling change only the picture.
    ``round_digits`` is the exception: it is the resolution the geometry is
    re-regularised onto before drawing, so a coarser request yields fewer, simpler
    polygons rather than the same polygons printed with fewer digits.
    """

    #: Decimal places for drawn coordinates, or ``None`` to inherit the
    #: preview's own model resolution.  Inheriting is the default because the
    #: drawing has to show the polygons the JSON reports; asking for a different
    #: precision here re-regularises the geometry at that precision rather than
    #: rounding live coordinates (see :func:`odbpp.export.regularise_preview`).
    round_digits: Optional[int] = None
    #: Dark background: copper reads better on it, and it is what EDA tools use.
    background: str = "#101418"
    #: Include layers that composed to nothing, so the layer list is complete and
    #: an engineer can see that e.g. a zero-width outline layer contributed
    #: nothing rather than assuming it was forgotten.
    include_empty: bool = True


#: Base colour per layer type, chosen so the common stackup reads in the usual
#: way: copper warm, mask green, silk white, drills cyan.
_TYPE_COLOURS: dict[str, tuple[str, str, str]] = {
    "SIGNAL":        ("#e05c4a", "#c8341f", "#ff8a72"),
    "POWER_GROUND":  ("#b8452f", "#8e2a17", "#d9634a"),
    "MIXED":         ("#c9764a", "#a3542c", "#e59a6f"),
    "DIELECTRIC":    ("#4a5a44", "#38452f", "#5f7355"),
    "SOLDER_MASK":   ("#3f8f5a", "#2b6c40", "#5fb47c"),
    "SOLDER_PASTE":  ("#9aa4ad", "#767f88", "#c0c8d0"),
    "SILK_SCREEN":   ("#e8e8e0", "#c4c4bb", "#ffffff"),
    "DRILL":         ("#57c7d4", "#2f9dab", "#8ee6f0"),
    "ROUT":          ("#c457c7", "#9a2f9d", "#e08ee6"),
    "DOCUMENT":      ("#c9a04a", "#a07c2b", "#e6c076"),
    "COMPONENT":     ("#9b7fd4", "#7559b0", "#bca6ea"),
    "MASK":          ("#7a6ca8", "#584c85", "#9d90c9"),
    "CONDUCTIVE_PASTE": ("#8f8f5a", "#6c6c3c", "#b4b47c"),
    "UNKNOWN":       ("#7f8c8d", "#5d6869", "#a3b0b1"),
}

#: Paint order, back to front.  Dielectrics and masks are substrates; drills and
#: documents are annotations and belong on top.
_Z_RANK: dict[str, int] = {
    "DIELECTRIC": 0,
    "MASK": 1,
    "CONDUCTIVE_PASTE": 2,
    "SIGNAL": 3,
    "POWER_GROUND": 3,
    "MIXED": 3,
    "SOLDER_MASK": 4,
    "SOLDER_PASTE": 5,
    "SILK_SCREEN": 6,
    "COMPONENT": 7,
    "DOCUMENT": 8,
    "DRILL": 9,
    "ROUT": 9,
    "UNKNOWN": 5,
}

#: Opacity by paint rank.  Without this the default "everything on" view is
#: dominated by whichever layer covers the most area -- an inner plane layer
#: paints over the traces on the outer layers and the board reads as a solid
#: rectangle.  Fading the deeper layers keeps the whole stack legible at once,
#: which is what the viewer is for; the layer toggles exist for when it is not.
_Z_OPACITY: dict[int, float] = {
    0: 0.40,   # dielectric substrate
    1: 0.50,   # stiffener / mask
    3: 0.72,   # copper
    4: 0.55,   # solder mask
    5: 0.70,   # paste
    6: 0.95,   # silkscreen
    7: 0.55,   # component
    8: 0.60,   # documentation
    9: 1.00,   # drills and routs, on top and opaque
}


def layer_opacity(preview: LayerPreview) -> float:
    return _Z_OPACITY.get(_Z_RANK.get(preview.type_name, 5), 0.72)


def _blend(hex_colour: str, other: str, amount: float) -> str:
    a = [int(hex_colour[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(other[i:i + 2], 16) for i in (1, 3, 5)]
    mixed = [round(x + (y - x) * amount) for x, y in zip(a, b)]
    return "#" + "".join(f"{v:02x}" for v in mixed)


def layer_colour(preview: LayerPreview, index: int = 0, *,
                 selected: bool = False) -> str:
    """Deterministic colour for a layer.

    Layers of the same type are separated by a small lightness step so that
    ``SIGNAL_1``..``SIGNAL_10`` are distinguishable, and the step is derived from
    the layer's matrix row rather than from iteration order so the colours are
    stable across runs and across the SVG and HTML outputs.
    """
    palette = _TYPE_COLOURS.get(preview.type_name, _TYPE_COLOURS["UNKNOWN"])
    base = palette[0]
    row = getattr(preview.layer.ref, "row", index) or index

    # Alternate around the base colour: every 4th layer returns to it.
    step = ((row - 1) % 4) / 3.0
    if step == 0.0:
        colour = base
    elif step < 0.5:
        colour = _blend(base, "#ffffff", 0.18 * (step * 2))
    else:
        colour = _blend(base, "#000000", 0.22 * ((step - 0.5) * 2))
    return _blend(colour, "#ffffff", 0.22) if selected else colour


def _fmt(value: float, digits: int) -> str:
    """Format a coordinate, trimming trailing zeros.

    Trimming is not cosmetic: a busy layer has tens of thousands of coordinate
    pairs, and ``12.7000`` costs 20% more bytes than ``12.7`` for no information.
    """
    text = f"{value:.{digits}f}"
    if digits > 0:
        text = text.rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


def path_data(rings: Sequence[Ring], digits: int) -> str:
    """Concatenate rings into one ``d`` attribute.

    Every ring starts with an absolute ``M``, which is what makes concatenation
    valid: subpaths are independent and ``Z`` closes each one.  The ring is
    already closed (its last point is not a repeat of its first), so ``Z`` adds
    the final edge.
    """
    parts: list[str] = []
    for ring in rings:
        points = ring.points
        if len(points) < 3:
            continue
        x0, y0 = points[0]
        chunk = [f"M{_fmt(x0, digits)} {_fmt(y0, digits)}"]
        for x, y in points[1:]:
            chunk.append(f"L{_fmt(x, digits)} {_fmt(y, digits)}")
        chunk.append("Z")
        parts.append("".join(chunk))
    return "".join(parts)


def _render_digits(preview: StepPreview, options: SvgOptions) -> int:
    """Coordinate resolution to draw *preview* at.

    An explicit ``SvgOptions.round_digits`` wins; otherwise the preview's model
    resolution is used, so the drawing shows the same polygons the JSON will.
    A hand-built preview with no recorded resolution falls back to the preview
    default.
    """
    if options.round_digits is not None:
        return max(0, int(options.round_digits))
    return _preview_digits(preview, None)


def _ordered(layers: Sequence[LayerPreview], options: SvgOptions) -> list[LayerPreview]:
    visible = [l for l in layers if options.include_empty or not l.is_empty]
    return sorted(visible, key=lambda l: (_Z_RANK.get(l.type_name, 5),
                                          l.layer.ref.row))


def _viewbox(preview: StepPreview, digits: int) -> tuple[float, float, float, float]:
    bounds = preview.bounds or (0.0, 0.0, 1.0, 1.0)
    x0, y0, x1, y1 = bounds
    width = max(x1 - x0, 1e-6)
    height = max(y1 - y0, 1e-6)
    pad = max(width, height) * 0.02
    return (x0 - pad, y0 - pad, width + 2 * pad, height + 2 * pad)


# ---------------------------------------------------------------------------
# SVG
# ---------------------------------------------------------------------------

def render_svg(preview: StepPreview, *, options: Optional[SvgOptions] = None,
               layers: Optional[Sequence[LayerPreview]] = None) -> str:
    """A standalone SVG of every visible layer.

    Layer groups carry ``data-name``/``data-type`` attributes and a stable ``id``
    so the same markup works both as a standalone file and as the body of the
    HTML viewer.
    """
    opts = options or SvgOptions()
    digits = _render_digits(preview, opts)
    # Draw the geometry at the resolution it is reported at: printing live
    # coordinates at a coarser precision is what turned a 0.00002 mm-wide pad
    # into a zero-width path whose JSON still claimed area.
    view = regularise_preview(preview, digits, diagnostics=preview.diagnostics)
    if layers is not None:
        by_name = {l.name: l for l in view.layers}
        source = [by_name[l.name] for l in layers if l.name in by_name]
    else:
        source = view.layers
    return _render_svg(view, opts, source, _viewbox(view, digits))


def _render_svg(view: StepPreview, opts: SvgOptions,
                source: Sequence[LayerPreview],
                viewport: tuple[float, float, float, float]) -> str:
    """Render a regularised view with a shared whole-board viewport."""
    digits = _render_digits(view, opts)
    chosen = _ordered(source, opts)

    x0, y0, width, height = viewport
    out: list[str] = []
    out.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="{_fmt(x0, digits)} {_fmt(y0, digits)} '
        f'{_fmt(width, digits)} {_fmt(height, digits)}" '
        f'class="odb-svg" preserveAspectRatio="xMidYMid meet">'
    )
    out.append(
        f'<rect x="{_fmt(x0, digits)}" y="{_fmt(y0, digits)}" '
        f'width="{_fmt(width, digits)}" height="{_fmt(height, digits)}" '
        f'fill="{opts.background}"/>'
    )

    # Two nested groups, deliberately.  The outer one is the viewer's pan/zoom
    # handle and stays identity in the standalone file; the inner one carries the
    # single y-flip that turns model coordinates into SVG coordinates.  Keeping
    # them apart means the viewer never has to reconstruct the flip, and the flip
    # (which negates the winding sign) is applied after pan/zoom rather than
    # interleaved with it.
    top = y0 + (y0 + height)
    out.append('<g id="odb-root">')
    out.append(f'<g id="odb-flip" transform="translate(0 {_fmt(top, digits)}) scale(1 -1)" '
               f'fill-rule="nonzero">')

    for index, layer in enumerate(chosen):
        colour = layer_colour(layer, index)
        data = path_data(layer.rings, digits)
        opacity = layer_opacity(layer)
        extent = layer.z_extent()
        # Z travels with the drawing, not only with the JSON: a layer whose
        # extent is unknown says so, so a reader never mistakes an assumed-zero
        # gap for a measured one.
        z_attributes = "" if extent is None else (
            f'data-z-start="{_fmt(extent.start_mm, digits)}" '
            f'data-z-end="{_fmt(extent.end_mm, digits)}" '
            f'data-z-known="{str(extent.thickness_known).lower()}" '
        )
        attributes = (
            f'id="L{index}" class="odb-layer" '
            f'data-name="{_escape(layer.name)}" '
            f'data-type="{layer.type_name}" '
            f'data-rings="{len(layer.rings)}" '
            f'data-area="{layer.area_mm2:.3f}" '
            f'data-colour="{colour}" '
            f'{z_attributes}'
            f'fill="{colour}" fill-opacity="{opacity:g}" style="color:{colour}"'
        )
        if not data:
            # Keep the element so the layer list matches the drawing, and so the
            # emptiness is visible rather than looking like a missing layer.
            out.append(f'<g {attributes}></g>')
            continue
        out.append(f'<g {attributes}><path d="{data}"/></g>')

    out.append("</g></g></svg>")
    return "".join(out)


def _escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))


# ---------------------------------------------------------------------------
# HTML viewer
# ---------------------------------------------------------------------------

_CSS = """
*{box-sizing:border-box}
html,body{margin:0;height:100%;font:13px/1.45 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  background:#0b0e11;color:#c9d1d9}
#app{display:flex;height:100%}
#side{width:310px;min-width:310px;display:flex;flex-direction:column;border-right:1px solid #232a31;
  background:#0f1317}
#head{padding:10px 12px;border-bottom:1px solid #232a31}
#head h1{margin:0 0 4px;font-size:13px;font-weight:600;color:#e6edf3}
#head .meta{color:#7d8590;font-size:11px;word-break:break-all}
#tools{display:flex;flex-wrap:wrap;gap:4px;padding:8px 10px;border-bottom:1px solid #232a31}
#tools button{flex:0 0 auto;background:#1a2129;color:#c9d1d9;border:1px solid #2c353f;
  border-radius:4px;padding:3px 7px;font:inherit;font-size:11px;cursor:pointer}
#tools button:hover{background:#232c36;border-color:#3d4954}
#tools button.on{background:#1f6feb;border-color:#1f6feb;color:#fff}
#sorter{display:flex;align-items:center;gap:7px;padding:7px 10px;border-bottom:1px solid #232a31;
  color:#7d8590;font-size:11px}
#sort-order{flex:1;min-width:0;background:#1a2129;color:#c9d1d9;border:1px solid #2c353f;
  border-radius:4px;padding:3px 5px;font:inherit;cursor:pointer}
#layers{flex:1;overflow:auto;padding:4px 0}
.row{display:flex;align-items:center;gap:7px;padding:3px 10px;cursor:pointer;white-space:nowrap}
.row:hover{background:#161c22}
.row.sel{background:#1c2733}
.row .chip{width:10px;height:10px;border-radius:2px;flex:0 0 auto;border:1px solid #0006}
.row .nm{flex:1;overflow:hidden;text-overflow:ellipsis}
.row .ty{color:#6e7681;font-size:10.5px}
.row .ct{color:#586069;font-size:10.5px;text-align:right;min-width:44px}
.row input{margin:0;flex:0 0 auto}
#stage{position:relative;flex:1;overflow:hidden;background:#0b0e11}
#stage svg{width:100%;height:100%;display:block;cursor:grab}
#stage svg.drag{cursor:grabbing}
#status{position:absolute;left:0;right:0;bottom:0;padding:3px 10px;display:flex;gap:14px;
  background:#0b0e11cc;border-top:1px solid #232a31;color:#7d8590;font-size:11px;pointer-events:none}
#status b{color:#c9d1d9;font-weight:500}
#info{position:absolute;right:10px;top:10px;max-width:290px;padding:8px 10px;background:#0f1317e6;
  border:1px solid #232a31;border-radius:5px;font-size:11px;color:#8b949e;display:none}
#info h2{margin:0 0 5px;font-size:12px;color:#e6edf3}
#info table{border-collapse:collapse}
#info td{padding:1px 0}
#info td:first-child{color:#6e7681;padding-right:10px}
body.wire .odb-layer path{fill:none;stroke:currentColor;stroke-width:1px;vector-effect:non-scaling-stroke}
#empty{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;
  color:#484f58;pointer-events:none}
"""

_JS = r"""
(function(){
  var svg  = document.querySelector('.odb-svg');
  var root = document.getElementById('odb-root');      // pan/zoom handle
  var vb   = svg.getAttribute('viewBox').split(/[\s,]+/).map(Number);
  var t    = {k:1, x:0, y:0};                          // in viewBox units

  function apply(){
    root.setAttribute('transform',
      'translate(' + t.x + ' ' + t.y + ') scale(' + t.k + ')');
  }

  // Model <-> screen go through the root group's CTM, which already contains
  // the viewBox mapping *and* the y-flip of the inner group.  Recomputing the
  // transform by hand is how pan/zoom ends up subtly wrong under letterboxing.
  function toModel(cx, cy){
    var p = svg.createSVGPoint(); p.x = cx; p.y = cy;
    return p.matrixTransform(root.getScreenCTM().inverse());
  }
  function toScreen(x, y){
    var p = svg.createSVGPoint(); p.x = x; p.y = y;
    return p.matrixTransform(root.getScreenCTM());
  }
  // Pixels per viewBox unit.  Root translations are in viewBox units, so screen
  // deltas have to be divided by this before being added to t.
  function vbScale(){ return svg.getScreenCTM().a || 1; }

  function pan(dx, dy){
    var s = vbScale();
    t.x += dx / s; t.y += dy / s;
    apply();
  }

  function zoomAt(cx, cy, factor){
    var before = toModel(cx, cy);
    t.k = Math.max(0.02, Math.min(5000, t.k * factor));
    apply();
    var now = toScreen(before.x, before.y);
    var s = vbScale();
    t.x += (cx - now.x) / s;
    t.y += (cy - now.y) / s;
    apply();
  }

  function fit(){ t = {k:1, x:0, y:0}; apply(); }

  var dragging = false, lastX = 0, lastY = 0;
  svg.addEventListener('mousedown', function(e){
    if (e.button !== 0) return;
    dragging = true; lastX = e.clientX; lastY = e.clientY;
    svg.classList.add('drag');
    e.preventDefault();
  });
  window.addEventListener('mousemove', function(e){
    var m = toModel(e.clientX, e.clientY);
    document.getElementById('pos').textContent =
      'x ' + m.x.toFixed(3) + '   y ' + m.y.toFixed(3) + ' mm';
    if (!dragging) return;
    pan(e.clientX - lastX, e.clientY - lastY);
    lastX = e.clientX; lastY = e.clientY;
  });
  window.addEventListener('mouseup', function(){
    dragging = false; svg.classList.remove('drag');
  });
  svg.addEventListener('wheel', function(e){
    e.preventDefault();
    zoomAt(e.clientX, e.clientY, e.deltaY < 0 ? 1.15 : 1/1.15);
  }, {passive:false});

  document.getElementById('zin').onclick  = function(){
    zoomAt(svg.clientWidth/2, svg.clientHeight/2, 1.25); };
  document.getElementById('zout').onclick = function(){
    zoomAt(svg.clientWidth/2, svg.clientHeight/2, 1/1.25); };
  document.getElementById('fit').onclick  = fit;

  // ---- layer list -------------------------------------------------------
  var rows = Array.prototype.slice.call(document.querySelectorAll('.row'));
  var layerList = document.getElementById('layers');
  document.getElementById('sort-order').addEventListener('change', function(e){
    var sorted = rows.slice();
    if (e.target.value === 'stackup') {
      sorted.sort(function(a, b){ return Number(a.dataset.row) - Number(b.dataset.row); });
    }
    sorted.forEach(function(row){ layerList.appendChild(row); });
  });
  var groups = {};
  Array.prototype.forEach.call(document.querySelectorAll('.odb-layer'), function(g){
    groups[g.dataset.name] = g;
  });

  function setVisible(name, on){
    var g = groups[name];
    if (g) g.style.display = on ? '' : 'none';
  }

  var selected = null;
  function select(name){
    selected = name;
    rows.forEach(function(r){ r.classList.toggle('sel', r.dataset.name === name); });
    var el = document.getElementById('info');
    var row = rows.filter(function(r){ return r.dataset.name === name; })[0];
    if (!row) { el.style.display = 'none'; return; }
    el.innerHTML =
      '<h2>' + row.dataset.name + '</h2><table>' +
      '<tr><td>type</td><td>' + row.dataset.type + '</td></tr>' +
      '<tr><td>rings</td><td>' + row.dataset.rings + '</td></tr>' +
      '<tr><td>area</td><td>' + row.dataset.area + ' mm&sup2;</td></tr>' +
      '<tr><td>z</td><td>' + (row.dataset.zStart === undefined
          ? 'not on the board'
          : row.dataset.zStart + ' .. ' + row.dataset.zEnd + ' mm' +
            (row.dataset.zKnown === 'false' ? ' (partly assumed)' : '')) +
      '</td></tr>' +
      '<tr><td>colour</td><td>' + row.dataset.colour + '</td></tr>' +
      '</table>';
    el.style.display = 'block';
  }

  rows.forEach(function(row){
    var box = row.querySelector('input');
    var name = row.dataset.name;
    box.addEventListener('change', function(){ setVisible(name, box.checked); });
    row.addEventListener('click', function(e){
      if (e.target !== box) { box.checked = !box.checked; setVisible(name, box.checked); }
      select(name);
    });
    setVisible(name, box.checked);
  });

  function bulk(mode){
    rows.forEach(function(row){
      var box = row.querySelector('input');
      var on = box.checked;
      if (mode === 'all')          on = true;
      else if (mode === 'none')    on = false;
      else if (mode === 'copper')  on = /SIGNAL|POWER_GROUND|MIXED/.test(row.dataset.type);
      else if (mode === 'isolate') on = (row.dataset.name === selected);
      else if (mode === 'invert')  on = !box.checked;
      box.checked = on;
      setVisible(row.dataset.name, on);
    });
  }
  document.getElementById('all').onclick     = function(){ bulk('all'); };
  document.getElementById('none').onclick    = function(){ bulk('none'); };
  document.getElementById('copper').onclick  = function(){ bulk('copper'); };
  document.getElementById('isolate').onclick = function(){ bulk('isolate'); };
  document.getElementById('invert').onclick  = function(){ bulk('invert'); };

  var wire = document.getElementById('wirebtn');
  function toggleWire(){
    var on = document.body.classList.toggle('wire');
    wire.classList.toggle('on', on);
  }
  wire.onclick = toggleWire;

  window.addEventListener('keydown', function(e){
    if (e.target && /INPUT|TEXTAREA/.test(e.target.tagName)) return;
    if (e.key === 'f' || e.key === 'F') fit();
    else if (e.key === 'a') bulk('all');
    else if (e.key === 'n') bulk('none');
    else if (e.key === 'c') bulk('copper');
    else if (e.key === 'i') bulk('invert');
    else if (e.key === 'w' || e.key === 'W') toggleWire();
  });

  apply();
  if (rows.length) select(rows[0].dataset.name);
})();
"""


def _layer_rows(preview: StepPreview, options: SvgOptions,
                ordered: Sequence[LayerPreview]) -> str:
    rows: list[str] = []
    for index, layer in enumerate(ordered):
        colour = layer_colour(layer, index)
        checked = "" if layer.is_empty else " checked"
        extent = layer.z_extent()
        z_attributes = "" if extent is None else (
            f'data-z-start="{extent.start_mm:.3f}" '
            f'data-z-end="{extent.end_mm:.3f}" '
            f'data-z-known="{str(extent.thickness_known).lower()}" '
        )
        rows.append(
            f'<div class="row" data-name="{_escape(layer.name)}" '
            f'data-type="{layer.type_name}" data-row="{layer.layer.ref.row}" '
            f'data-rings="{len(layer.rings)}" '
            f'data-area="{layer.area_mm2:.3f}" data-colour="{colour}" '
            f'{z_attributes}>'
            f'<input type="checkbox"{checked}>'
            f'<span class="chip" style="background:{colour}"></span>'
            f'<span class="nm">{_escape(layer.name)}</span>'
            f'<span class="ty">{layer.type_name}</span>'
            f'<span class="ct">{len(layer.rings)}</span>'
            f'</div>'
        )
    return "".join(rows)


def _thickness_note(preview: StepPreview) -> str:
    """Board thickness from the Z table, or ``""`` when nothing is known.

    Only shown when at least one layer has a known extent: a value assembled
    entirely from assumed zeros would be a fabrication, and the header is
exactly where a fabricated number would be believed.
    """
    stackup = preview.stackup
    if stackup is None:
        return ""
    extents = [stackup.z_extent(layer) for layer in stackup.board_layers]
    known = [e for e in extents if e is not None and e.thickness_known]
    if not known:
        return ""
    total = max(e.end_mm for e in known)
    return f"~{total:.3f} mm thick (top..{total:.2f} mm)"


def render_html(preview: StepPreview, *, options: Optional[SvgOptions] = None,
                title: Optional[str] = None,
                source: str = "") -> str:
    """A single self-contained HTML page: SVG plus pan/zoom and layer toggles."""
    opts = options or SvgOptions()
    # Regularise once and use the same view for the page's metadata and for the
    # inlined SVG, so the header cannot describe rings or bounds the drawing no
    # longer shows.
    view = regularise_preview(preview, _render_digits(preview, opts),
                              diagnostics=preview.diagnostics)
    ordered = _ordered(list(view.layers), opts)
    body = render_svg(view, options=opts, layers=ordered)

    return _render_html(view, opts, ordered, body, title=title, source=source)


def _render_html(view: StepPreview, opts: SvgOptions,
                 ordered: Sequence[LayerPreview], body: str, *,
                 title: Optional[str] = None, source: str = "") -> str:
    """Wrap an already rendered SVG, using metadata from the same view."""
    import html as _html

    name = title or view.step.name
    bounds = view.bounds or (0, 0, 0, 0)
    size = f"{bounds[2] - bounds[0]:.2f} x {bounds[3] - bounds[1]:.2f} mm"
    empty = [l.name for l in ordered if l.is_empty]
    failed = list(view.failed_layers)

    head_meta = " &middot; ".join(filter(None, [
        _html.escape(source or ""),
        f"step {_html.escape(view.step.name)}",
        f"input units {view.units_in}",
        f"{len(view.layers)} layers",
        f"{view.total_polygons} rings",
        size,
        _thickness_note(view),
    ]))

    # Empty and failed layers are called out separately and never merged: an
    # empty layer is usually intentional (zero-width artwork) while a failed one
    # means the drawing is incomplete, and a reader needs to know which.
    warning = ""
    if empty:
        warning += (
            '<div class="meta" style="color:#d29922;margin-top:4px">'
            f'{len(empty)} layer(s) composed to nothing: '
            f'{_html.escape(", ".join(empty[:6]))}'
            f'{"..." if len(empty) > 6 else ""}'
            " &mdash; often zero-width (<code>r0</code>) artwork</div>"
        )
    if failed:
        warning += (
            '<div class="meta" style="color:#f85149;margin-top:4px">'
            f'{len(failed)} layer(s) could not be composed and are absent: '
            f'{_html.escape(", ".join(failed[:6]))}'
            f'{"..." if len(failed) > 6 else ""}</div>'
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_html.escape(name)} &middot; ODB++ preview</title>
<style>{_CSS}</style>
</head>
<body>
<div id="app">
  <div id="side">
    <div id="head">
      <h1>{_html.escape(name)}</h1>
      <div class="meta">{head_meta}</div>
      {warning}
    </div>
    <div id="tools">
      <button id="all" title="show every layer (a)">all</button>
      <button id="none" title="hide every layer (n)">none</button>
      <button id="copper" title="copper layers only (c)">copper</button>
      <button id="isolate" title="show only the selected layer">isolate</button>
      <button id="invert" title="invert the current selection">invert</button>
      <button id="wirebtn" title="outline mode (w)">wireframe</button>
      <button id="zin" title="zoom in">+</button>
      <button id="zout" title="zoom out">&minus;</button>
      <button id="fit" title="fit to window (f)">fit</button>
    </div>
    <label id="sorter" for="sort-order">Layer order
      <select id="sort-order" aria-label="Layer order">
        <option value="type">By type</option>
        <option value="stackup">Top to bottom</option>
      </select>
    </label>
    <div id="layers">{_layer_rows(view, opts, ordered)}</div>
  </div>
  <div id="stage">
    {body}
    <div id="info"></div>
    <div id="status">
      <span><b id="pos">&ndash;</b></span>
      <span>drag to pan &middot; wheel to zoom &middot; f fit &middot; w wireframe</span>
    </div>
  </div>
</div>
<script>{_JS}</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# writing
# ---------------------------------------------------------------------------

@dataclass
class Outputs:
    """Files written by :func:`write_outputs`."""

    json: Optional[str] = None
    svg: Optional[str] = None
    html: Optional[str] = None
    layer_svgs: list[str] = field(default_factory=list)

    def all_paths(self) -> list[str]:
        return [p for p in (self.json, self.svg, self.html) if p] + self.layer_svgs


def write_outputs(
    preview: StepPreview,
    job,
    out_dir: str,
    *,
    svg_options: Optional[SvgOptions] = None,
    preview_options: Optional[PreviewOptions] = None,
    want_json: bool = True,
    want_svg: bool = True,
    want_html: bool = True,
    per_layer_svg: bool = False,
    stem: Optional[str] = None,
    include_diagnostics: bool = True,
) -> Outputs:
    """Write the preview, returning the paths produced.

    One resolution is chosen per artefact family and the geometry is
    re-regularised onto it before writing, so what the JSON reports, what the
    SVG draws and what the HTML lists are the same polygons.  By default both
    families follow the preview's own model resolution; an explicit
    ``preview_options``/``svg_options`` precision overrides it, and a mismatch
    between the two is honoured rather than hidden.
    """
    import json as _json
    import os

    from .export import step_to_json

    # JSON follows the preview's resolution unless the caller asks otherwise; the
    # drawing follows the JSON unless the caller asks otherwise.
    json_digits = _preview_digits(
        preview, preview_options.round_digits if preview_options is not None else None)
    if svg_options is not None and svg_options.round_digits is not None:
        draw_digits = max(0, int(svg_options.round_digits))
    else:
        draw_digits = json_digits
    opts = (replace(svg_options, round_digits=draw_digits)
            if svg_options is not None else SvgOptions(round_digits=draw_digits))

    json_view = regularise_preview(preview, json_digits,
                                   diagnostics=preview.diagnostics)
    draw_view = (json_view if draw_digits == json_digits else
                 regularise_preview(preview, draw_digits,
                                    diagnostics=preview.diagnostics))
    stem = stem or preview.step.name or "preview"
    os.makedirs(out_dir, exist_ok=True)

    outputs = Outputs()

    if want_json:
        path = os.path.join(out_dir, f"{stem}.json")
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            _json.dump(step_to_json(job, json_view,
                                    round_digits=json_digits,
                                    include_diagnostics=include_diagnostics),
                       handle, ensure_ascii=False, indent=1)
            handle.write("\n")
        outputs.json = path

    # The standalone SVG and the viewer contain exactly the same drawing.
    # Keep reuse local to this write: previews and rendering options can change
    # between calls, so a persistent render cache would be unsafe.
    viewport = (_viewbox(draw_view, draw_digits)
                if want_svg or want_html or per_layer_svg else None)
    drawing = (_render_svg(draw_view, opts, draw_view.layers, viewport)
               if want_svg or want_html else "")
    if want_svg:
        path = os.path.join(out_dir, f"{stem}.svg")
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(drawing)
        outputs.svg = path

    if want_html:
        path = os.path.join(out_dir, f"{stem}.html")
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(_render_html(draw_view, opts, _ordered(draw_view.layers, opts),
                                      drawing, source=getattr(job, "source_path", "")))
        outputs.html = path

    del drawing

    if per_layer_svg:
        layer_dir = os.path.join(out_dir, "layers")
        os.makedirs(layer_dir, exist_ok=True)
        for index, layer in enumerate(_ordered(draw_view.layers, opts)):
            safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in layer.name)
            path = os.path.join(layer_dir, f"{safe}.svg")
            with open(path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(_render_svg(draw_view, opts, [layer], viewport))
            outputs.layer_svgs.append(path)

    return outputs
