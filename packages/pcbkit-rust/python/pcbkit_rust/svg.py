"""SVG and a self-contained HTML viewer (data-flow step 3).

Dielectric drill cutouts are made on a local drawing copy. JSON keeps the uncut
material geometry; copper artwork is never changed by DRILL projections.

The output is not a picture of the files -- it is the composed geometry from
:mod:`pcbkit.export`, drawn.  That distinction is the whole point of the pipeline:
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
from typing import Mapping, Optional, Sequence

from .clipper import Ring, region_from_geometry, snap_to_grid
from .drawing import dielectric_drill_cutouts
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
    #: rounding live coordinates (see :func:`pcbkit.export.regularise_preview`).
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
    view = dielectric_drill_cutouts(view, None if layers is None else {l.name for l in layers})
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

    if view.step.profile is not None:
        profile, _ = snap_to_grid(region_from_geometry(view.step.profile), digits)
        out.append(f'<path id="odb-profile" d="{path_data(profile.rings, digits)}" '
                   'fill="none" stroke="#c9d1d9" stroke-width="1" '
                   'vector-effect="non-scaling-stroke" pointer-events="none"/>')
    out.append('<g id="odb-domains" fill="none" stroke="#56d6a0" '
               'stroke-dasharray="6 4" pointer-events="none">')
    for index, layer in enumerate(chosen):
        profile = layer.layer.profile if layer.layer.profile is not None else view.step.profile
        if profile is not None and layer.layer.profile_error is None:
            domain, _ = snap_to_grid(region_from_geometry(profile), digits)
            out.append(f'<path data-name="{_escape(layer.name)}" '
                       f'd="{path_data(domain.rings, digits)}" style="display:none" '
                       'stroke-width="1.5" vector-effect="non-scaling-stroke"/>')
    out.append('</g><g id="odb-measure" pointer-events="none" '
               'stroke="#ffd166" fill="none" stroke-width="2" '
               'vector-effect="non-scaling-stroke"></g>')
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
#stage .odb-layer,#odb-profile,#odb-domains,#odb-measure{pointer-events:none}
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
#searchbar{padding:8px 10px;border-bottom:1px solid #232a31}
#layer-search{width:100%;background:#1a2129;color:#c9d1d9;border:1px solid #2c353f;padding:6px}
#search-count{font-size:11px;color:#7d8590;padding-top:4px}
.row:focus-visible{outline:1px solid #58a6ff;outline-offset:-1px}
.row[hidden]{display:none}
#info{max-height:75%;overflow:auto;max-width:390px}
#info td{overflow-wrap:anywhere;white-space:normal}
#info td:first-child{white-space:nowrap}
#matrix{width:90vw;max-width:1200px;max-height:85vh;background:#0f1317;color:#c9d1d9;
  border:1px solid #3d4954;border-radius:8px;padding:18px;overflow:auto}
#matrix::backdrop{background:#0009}
#matrix table{border-collapse:collapse;width:100%;font-size:11px}
#matrix th,#matrix td{padding:6px 8px;text-align:left;border-bottom:1px solid #232a31}
#matrix th{position:sticky;top:0;background:#1a2129}
#matrix button{float:right;cursor:pointer}
#matrix-layout{position:relative;padding-left:var(--span-width);min-width:950px}
#matrix-spans{position:absolute;left:0;top:0;width:var(--span-width);overflow:visible}
.drill-span{color:#8b949e;cursor:pointer;outline:none}
.drill-span .span-line{stroke:currentColor;stroke-width:2;fill:none}
.drill-span .span-hit{stroke:transparent;stroke-width:14;fill:none}
.drill-span[aria-pressed="true"]{color:#ff4d4f}
.drill-span:focus-visible .span-line{stroke-width:4}
.matrix-row{cursor:pointer}
.matrix-row:hover{background:#1a2129}
.matrix-row[aria-selected="true"]{background:#303944}
.matrix-row.span-endpoint td:nth-child(2){color:#ff7477;background:#3d2025}
.matrix-row:focus-visible{outline:1px solid #58a6ff;outline-offset:-1px}
#matrix-selection{color:#ff7477;min-height:1.5em}
#measure-value{color:#ffd166}
#overview{height:130px;border-top:1px solid #232a31;padding:6px;flex-shrink:0}
#overview svg{width:100%;height:100%;display:block;cursor:crosshair}
@media(max-width:700px){
  #app{flex-direction:column}
  #side{width:100%;min-width:0;max-height:45%;border-right:0;border-bottom:1px solid #232a31}
  #head .meta{display:none}
  #layers{min-height:60px}
  #overview{display:none}
  #stage{min-height:45%}
  #info{max-width:calc(100% - 20px);max-height:55%}
  #status{flex-wrap:wrap;gap:4px}
}
"""

_JS = r"""
(function(){
  var svg  = document.querySelector('.odb-svg');
  var root = document.getElementById('odb-root');      // pan/zoom handle
  var flip = document.getElementById('odb-flip');
  var t    = {k:1, x:0, y:0};                          // in viewBox units
  var pos = document.getElementById('pos');
  var frame = null, transformDirty = false, positionText = null;
  var modelMatrix = flip.transform.baseVal.consolidate().matrix;

  function apply(){
    root.setAttribute('transform',
      'translate(' + t.x + ' ' + t.y + ') scale(' + t.k + ')');
    transformDirty = false;
  }

  // Keep every input delta, but paint at most once per animation frame. The
  // logical transform is also used for coordinates while a paint is pending.
  function renderFrame(){
    frame = null;
    if(transformDirty) apply();
    if(positionText !== null){
      if(pos.textContent !== positionText) pos.textContent = positionText;
      positionText = null;
    }
  }
  function schedule(){if(frame === null) frame = requestAnimationFrame(renderFrame);}
  function changed(){transformDirty = true; schedule();}
  function flushTransform(){if(transformDirty) apply();}

  // Read the browser's viewBox CTM (including letterboxing), then apply the
  // logical root transform. No write/read/write CTM round trip is needed.
  function rootScreenMatrix(base){
    // SVG transform lists store float32 values. Match that representation even
    // before the scheduled DOM write, including coordinate-display rounding.
    return (base || svg.getScreenCTM()).translate(Math.fround(t.x),Math.fround(t.y)).scale(Math.fround(t.k));
  }
  function toRoot(cx, cy, base){
    var p = svg.createSVGPoint(); p.x = cx; p.y = cy;
    return p.matrixTransform(rootScreenMatrix(base).inverse());
  }
  function toModel(cx, cy){
    var p = svg.createSVGPoint(); p.x = cx; p.y = cy;
    return p.matrixTransform(rootScreenMatrix().multiply(modelMatrix).inverse());
  }
  function toScreen(x, y, base){
    var p = svg.createSVGPoint(); p.x = x; p.y = y;
    return p.matrixTransform(rootScreenMatrix(base));
  }
  // Pixels per viewBox unit.  Root translations are in viewBox units, so screen
  // deltas have to be divided by this before being added to t.
  function vbScale(){ return svg.getScreenCTM().a || 1; }

  function pan(dx, dy){
    var s = vbScale();
    t.x += dx / s; t.y += dy / s;
    changed();
  }

  function zoomAt(cx, cy, factor){
    var base = svg.getScreenCTM();
    var before = toRoot(cx, cy, base);
    t.k = Math.max(0.02, Math.min(5000, t.k * factor));
    var now = toScreen(before.x, before.y, base);
    var s = base.a || 1;
    t.x += (cx - now.x) / s;
    t.y += (cy - now.y) / s;
    changed();
  }

  function fit(){ t = {k:1, x:0, y:0}; changed(); }

  var dragging = false, lastX = 0, lastY = 0;
  var measuring = false, measureStart = null;
  var measureGroup = document.getElementById('odb-measure');
  svg.addEventListener('mousedown', function(e){
    if (e.button !== 0) return;
    if (measuring) {
      var point = toModel(e.clientX,e.clientY);
      if (!measureStart) {
        measureStart = point; measureGroup.replaceChildren();
        document.getElementById('measure-value').textContent = '选择终点';
      } else {
        var line = document.createElementNS(svg.namespaceURI,'line');
        line.setAttribute('x1',measureStart.x); line.setAttribute('y1',measureStart.y);
        line.setAttribute('x2',point.x); line.setAttribute('y2',point.y);
        line.setAttribute('vector-effect','non-scaling-stroke'); measureGroup.appendChild(line);
        var dx=point.x-measureStart.x, dy=point.y-measureStart.y;
        document.getElementById('measure-value').textContent =
          'ΔX '+dx.toFixed(3)+' · ΔY '+dy.toFixed(3)+' · 距离 '+Math.hypot(dx,dy).toFixed(3)+' mm';
        measureStart=null;
      }
      return;
    }
    dragging = true; lastX = e.clientX; lastY = e.clientY;
    svg.classList.add('drag');
    e.preventDefault();
  });
  window.addEventListener('mousemove', function(e){
    if (!svg.contains(e.target) && !dragging) return;
    var m = toModel(e.clientX, e.clientY);
    positionText =
      'x ' + m.x.toFixed(3) + '   y ' + m.y.toFixed(3) + ' mm';
    schedule();
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
    var r=svg.getBoundingClientRect(); zoomAt(r.left+r.width/2, r.top+r.height/2, 1.25); };
  document.getElementById('zout').onclick = function(){
    var r=svg.getBoundingClientRect(); zoomAt(r.left+r.width/2, r.top+r.height/2, 1/1.25); };
  document.getElementById('fit').onclick  = fit;

  // ---- layer list -------------------------------------------------------
  var rows = Array.prototype.slice.call(document.querySelectorAll('.row'));
  var rowsByName = new Map(), boxes = new Map(), searchText = new Map();
  rows.forEach(function(row){
    rowsByName.set(row.dataset.name,row);
    boxes.set(row,row.querySelector('input'));
    searchText.set(row,(row.dataset.name+' '+row.dataset.type).toLowerCase());
  });
  var layerList = document.getElementById('layers');
  document.getElementById('sort-order').addEventListener('change', function(e){
    var sorted = rows.slice();
    if (e.target.value === 'stackup') {
      sorted.sort(function(a, b){ return Number(a.dataset.row) - Number(b.dataset.row); });
    }
    var fragment = document.createDocumentFragment();
    sorted.forEach(function(row){ fragment.appendChild(row); });
    layerList.appendChild(fragment);
  });
  var groups = Object.create(null);
  Array.prototype.forEach.call(document.querySelectorAll('.odb-layer'), function(g){
    groups[g.dataset.name] = g;
  });

  function setVisible(name, on){
    var g = groups[name];
    var display = on ? '' : 'none';
    if (g && g.style.display !== display) g.style.display = display;
  }

  var selected = null;
  var selectedRow = null, infoName = null;
  function select(name){
    selected = name;
    var nextRow = rowsByName.get(name);
    if(selectedRow !== nextRow){
      if(selectedRow) selectedRow.classList.remove('sel');
      selectedRow = nextRow;
      if(selectedRow) selectedRow.classList.add('sel');
    }
    updateMatrixSelection();
    var el = document.getElementById('info');
    var row = selectedRow;
    if (!row) { el.style.display = 'none'; return; }
    if(infoName !== name){
    el.replaceChildren();
    var heading=document.createElement('h2'); heading.textContent=row.dataset.name; el.appendChild(heading);
    var table=document.createElement('table'); el.appendChild(table);
    var values=[['类型',row.dataset.type],['实体环',row.dataset.rings],['面积',row.dataset.area+' mm²'],
      ['Z',row.dataset.zStart === undefined ? '非板内层' : row.dataset.zStart+' .. '+row.dataset.zEnd+' mm'+
       (row.dataset.zKnown === 'false' ? ' (部分厚度为估计)' : '')],
      ['厚度来源',row.dataset.thicknessSource],['原始极性',row.dataset.polarity],
      ['计算域',row.dataset.domainSource],['形态',row.dataset.form],['钻孔跨度',row.dataset.span],
      ['输入图元',row.dataset.features]];
    values.forEach(function(pair){var tr=table.insertRow(); pair.forEach(function(v){tr.insertCell().textContent=v || '—';});});
    var histogram=document.getElementById('hist-'+row.dataset.row);
    if(histogram) el.appendChild(histogram.content.cloneNode(true));
    infoName = name;
    } else el.scrollTop = 0;
    el.style.display = document.getElementById('propertiesbtn').classList.contains('on') ? 'block' : 'none';
    updateDomain();
  }

  rows.forEach(function(row){
    var box = boxes.get(row);
    var name = row.dataset.name;
    box.addEventListener('change', function(){ setVisible(name, box.checked); });
    row.addEventListener('click', function(){ select(name); });
    row.addEventListener('dblclick', function(e){if(e.target !== box){select(name);bulk('isolate');}});
    row.addEventListener('keydown',function(e){if(e.target === row && e.key === 'Enter'){select(name);bulk('isolate');}});
    setVisible(name, box.checked);
  });

  function bulk(mode){
    if(mode === 'isolate' && !selected) return;
    rows.forEach(function(row){
      var box = boxes.get(row);
      var on = box.checked;
      if (mode === 'all')          on = true;
      else if (mode === 'none')    on = false;
      else if (mode === 'copper')  on = /SIGNAL|POWER_GROUND|MIXED/.test(row.dataset.type);
      else if (mode === 'isolate') on = (row.dataset.name === selected);
      else if (mode === 'invert')  on = !box.checked;
      if(box.checked !== on) box.checked = on;
      setVisible(row.dataset.name, on);
    });
  }
  document.getElementById('all').onclick     = function(){ bulk('all'); };
  document.getElementById('none').onclick    = function(){ bulk('none'); };
  document.getElementById('copper').onclick  = function(){ bulk('copper'); };
  document.getElementById('isolate').onclick = function(){ bulk('isolate'); };
  document.getElementById('invert').onclick  = function(){ bulk('invert'); };

  var domainPaths = Array.from(document.querySelectorAll('#odb-domains path'));
  function updateDomain(){
    var on = document.getElementById('domainbtn').classList.contains('on');
    domainPaths.forEach(function(p){
      var display = p.dataset.name === selected && on ? '' : 'none';
      if(p.style.display !== display) p.style.display = display;
    });
  }
  document.getElementById('domainbtn').onclick=function(){this.classList.toggle('on');updateDomain();};
  document.getElementById('profilebtn').onclick=function(){
    var on=this.classList.toggle('on'); var p=document.getElementById('odb-profile'); if(p)p.style.display=on?'':'none';
  };
  document.getElementById('measurebtn').onclick=function(){
    measuring=this.classList.toggle('on'); measureStart=null; measureGroup.replaceChildren();
    document.getElementById('measure-value').textContent=measuring?'选择测距起点':'';
    if(measuring){document.getElementById('info').style.display='none';document.getElementById('propertiesbtn').classList.remove('on');}
  };
  document.getElementById('propertiesbtn').onclick=function(){this.classList.toggle('on');select(selected);};
  // Span endpoints follow actual row centres, including wrapped text and resizing.
  var matrixRows=Array.from(document.querySelectorAll('.matrix-row'));
  var spans=Array.from(document.querySelectorAll('.drill-span'));
  var spanPaths = new Map();
  spans.forEach(function(s){spanPaths.set(s,Array.from(s.querySelectorAll('path')));});
  var spanEndpoints = new Set();
  spans.forEach(function(s){spanEndpoints.add(Number(s.dataset.start));spanEndpoints.add(Number(s.dataset.end));});
  function updateMatrixSelection(){
    var active=spans.find(function(s){return s.dataset.name===selected;});
    matrixRows.forEach(function(r,i){
      var state=r.dataset.name===selected?'true':'false';
      if(r.getAttribute('aria-selected') !== state)r.setAttribute('aria-selected',state);
      r.classList.toggle('span-endpoint',!!active &&
        (i===Number(active.dataset.start) || i===Number(active.dataset.end)));
    });
    spans.forEach(function(s){var state=s===active?'true':'false';
      if(s.getAttribute('aria-pressed') !== state)s.setAttribute('aria-pressed',state);});
    var current=matrixRows.find(function(r){return r.dataset.name===selected;});
    document.getElementById('matrix-selection').textContent=current && current.dataset.span ?
      current.dataset.name+'：'+current.dataset.span : '选择钻孔层查看起止层；红色箭头表示当前选择。';
  }
  function layoutSpans(){
    if(!document.getElementById('matrix').open)return;
    var layout=document.getElementById('matrix-layout'), box=layout.getBoundingClientRect();
    var height = layout.offsetHeight;
    var centres = new Map();
    spanEndpoints.forEach(function(i){var b=matrixRows[i].getBoundingClientRect();centres.set(i,b.top+b.height/2-box.top);});
    var spanSvg=document.getElementById('matrix-spans');
    if(spanSvg.getAttribute('height') !== String(height))spanSvg.setAttribute('height',height);
    spans.forEach(function(s){
      var y1=centres.get(Number(s.dataset.start)), y2=centres.get(Number(s.dataset.end));
      var x=12+Number(s.dataset.lane)*18, direction=y2>=y1?1:-1;
      var d='M '+(x-4)+' '+y1+' H '+(x+4)+' M '+x+' '+y1+' V '+y2+
        ' M '+(x-4)+' '+(y2-direction*6)+' L '+x+' '+y2+' L '+(x+4)+' '+(y2-direction*6);
      spanPaths.get(s).forEach(function(p){if(p.getAttribute('d') !== d)p.setAttribute('d',d);});
    });
  }
  matrixRows.concat(spans).forEach(function(el){
    el.addEventListener('click',function(){select(el.dataset.name);});
    el.addEventListener('keydown',function(e){if(e.key==='Enter'||e.key===' '){e.preventDefault();select(el.dataset.name);}});
  });
  new ResizeObserver(layoutSpans).observe(document.querySelector('#matrix-layout table'));
  document.getElementById('matrixbtn').onclick=function(){
    document.getElementById('matrix').showModal();updateMatrixSelection();layoutSpans();
  };
  document.getElementById('matrix-close').onclick=function(){document.getElementById('matrix').close();};
  document.getElementById('layer-search').oninput=function(){
    var term=this.value.trim().toLowerCase(), count=0;
    rows.forEach(function(row){var hidden=!searchText.get(row).includes(term);if(row.hidden !== hidden)row.hidden=hidden; if(!hidden)count++;});
    document.getElementById('search-count').textContent=count+' / '+rows.length+' 层';
  };
  document.getElementById('fit-layer').onclick=function(){
    var g=groups[selected]; if(!g || !g.querySelector('path'))return;
    setVisible(selected,true); boxes.get(rowsByName.get(selected)).checked=true;
    flushTransform();
    var bounds=g.getBoundingClientRect(), viewport=svg.getBoundingClientRect();
    if(bounds.width<=0 || bounds.height<=0)return;
    zoomAt(bounds.left+bounds.width/2,bounds.top+bounds.height/2,
           .9*Math.min(viewport.width/bounds.width,viewport.height/bounds.height));
    flushTransform();
    bounds=g.getBoundingClientRect();
    pan(viewport.left+viewport.width/2-bounds.left-bounds.width/2,
        viewport.top+viewport.height/2-bounds.top-bounds.height/2);
  };

  // Small overview uses only the profile, avoiding a second copy of copper.
  var overview=document.querySelector('#overview svg');
  var outline=document.getElementById('odb-profile');
  if(outline){var copy=outline.cloneNode(true);copy.removeAttribute('id');
    var og=document.createElementNS(svg.namespaceURI,'g');og.setAttribute('transform',flip.getAttribute('transform'));
    og.appendChild(copy);overview.appendChild(og);}
  overview.onclick=function(e){var p=overview.createSVGPoint();p.x=e.clientX;p.y=e.clientY;
    p=p.matrixTransform(overview.getScreenCTM().inverse());var screen=toScreen(p.x,p.y),v=svg.getBoundingClientRect();
    pan(v.left+v.width/2-screen.x,v.top+v.height/2-screen.y);};

  var wire = document.getElementById('wirebtn');
  function toggleWire(){
    var on = document.body.classList.toggle('wire');
    wire.classList.toggle('on', on);
  }
  wire.onclick = toggleWire;

  window.addEventListener('keydown', function(e){
    if (e.target && /INPUT|TEXTAREA|SELECT|BUTTON/.test(e.target.tagName) || document.getElementById('matrix').open) return;
    if (e.key === 'f' || e.key === 'F') fit();
    else if (e.key === 'a') bulk('all');
    else if (e.key === 'n') bulk('none');
    else if (e.key === 'c') bulk('copper');
    else if (e.key === 'i') bulk('invert');
    else if (e.key === 'w' || e.key === 'W') toggleWire();
    else if(e.key === 'Escape'){measureStart=null;measureGroup.replaceChildren();}
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
        profile_source = (f"error: {layer.layer.profile_error}" if layer.layer.profile_error else
                          "layer_profile" if layer.layer.profile is not None else
                          "step_profile" if preview.step.profile is not None else "missing")
        details = (
            f'data-context="{layer.layer.ref.context.value}" '
            f'data-polarity="{layer.layer.ref.polarity.value}" '
            f'data-form="{layer.layer.ref.form.value}" '
            f'data-domain-source="{_escape(profile_source)}" '
            f'data-thickness-source="{_escape(extent.source if extent else "")}" '
            f'data-span="{_escape(layer.layer.ref.start_name or "")} .. '
            f'{_escape(layer.layer.ref.end_name or "")}" '
            f'data-features="{layer.geometry.records}" '
        )
        rows.append(
            f'<div class="row" data-name="{_escape(layer.name)}" '
            f'data-type="{layer.type_name}" data-row="{layer.layer.ref.row}" '
            f'data-rings="{len(layer.rings)}" '
            f'data-area="{layer.area_mm2:.3f}" data-colour="{colour}" '
            f'{z_attributes}{details} tabindex="0" role="button" '
            'title="单击查看属性；双击单独显示；复选框控制可见性">'
            f'<input type="checkbox"{checked}>'
            f'<span class="chip" style="background:{colour}"></span>'
            f'<span class="nm">{_escape(layer.name)}</span>'
            f'<span class="ty">{layer.type_name}</span>'
            f'<span class="ct">{len(layer.rings)}</span>'
            f'</div>'
        )
    return "".join(rows)


def _matrix_panel(layers: Sequence[Mapping]) -> str:
    """Use authored matrix endpoints, never infer a through-board drill span."""
    ordered = sorted(layers, key=lambda layer: layer['row'])
    names: dict[str, list[int]] = {}
    for index, layer in enumerate(ordered):
        names.setdefault(layer['name'].casefold(), []).append(index)
    spans, errors = [], {}
    for index, layer in enumerate(ordered):
        if layer['type'].upper() != 'DRILL':
            continue
        endpoints = [names.get((layer.get(key) or '').casefold(), [])
                     for key in ('start_layer', 'end_layer')]
        if any(len(matches) != 1 for matches in endpoints):
            errors[index] = '跨度未解析：起止层缺失、不在当前矩阵或名称不唯一'
            continue
        start, end = (matches[0] for matches in endpoints)
        spans.append((min(start, end), max(start, end), index, start, end))
    # Disjoint intervals may share a lane; overlapping spans remain distinguishable.
    lane_ends, arrows = [], []
    for top, bottom, index, start, end in sorted(spans):
        lane = next((i for i, last in enumerate(lane_ends) if last < top), len(lane_ends))
        if lane == len(lane_ends):
            lane_ends.append(bottom)
        else:
            lane_ends[lane] = bottom
        layer = ordered[index]
        label = f"{layer['name']}：{ordered[start]['name']} → {ordered[end]['name']}"
        arrows.append(
            f'<g class="drill-span" data-name="{_escape(layer["name"])}" '
            f'data-start="{start}" data-end="{end}" data-lane="{lane}" '
            f'tabindex="0" role="button" aria-pressed="false" aria-label="{_escape(label)}">'
            f'<title>{_escape(label)}</title><path class="span-hit"/>'
            '<path class="span-line"/></g>')
    rows = []
    for index, layer in enumerate(ordered):
        extent = layer.get('z_mm')
        span = f"{layer.get('start_layer') or '—'} → {layer.get('end_layer') or '—'}"
        if index in errors:
            span += f"（{errors[index]}）"
        values = [str(layer['row']), layer['name'], layer['type'], layer['context'],
                  layer['polarity'], layer['form'], span,
                  '—' if extent is None else f"{extent['start_mm']:.6f} .. {extent['end_mm']:.6f}",
                  '—' if extent is None else ('已知' if extent['thickness_known'] else '估计'),
                  '—' if extent is None else extent['source']]
        details = span if layer['type'].upper() == 'DRILL' else ''
        rows.append(f'<tr class="matrix-row" data-name="{_escape(layer["name"])}" '
                    f'data-span="{_escape(details)}" tabindex="0" aria-selected="false">' +
                    ''.join(f'<td>{_escape(v)}</td>' for v in values) + '</tr>')
    width = max(44, len(lane_ends) * 18 + 8)
    return ('<dialog id="matrix" aria-label="层叠矩阵">'
            '<button id="matrix-close">关闭</button><h2>层叠矩阵</h2>'
            '<p>按原始矩阵顺序；Z 自叠层顶面向下，单位 mm。估计厚度需核对来源。</p>'
            '<p id="matrix-selection">选择钻孔层查看起止层；红色箭头表示当前选择。</p>'
            f'<div id="matrix-layout" style="--span-width:{width}px">'
            '<svg id="matrix-spans" xmlns="http://www.w3.org/2000/svg" aria-label="钻孔起止层箭头">' +
            ''.join(arrows) + '</svg><table role="grid" aria-label="层叠层列表"><thead><tr>' +
            ''.join(f'<th>{v}</th>' for v in
                    ('行','层名','类型','上下文','原始极性','形态','钻孔跨度','Z 起止 mm','厚度可信','来源')) +
            '</tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div></dialog>')


def _inspection_panels(ordered: Sequence[LayerPreview]) -> str:
    """Inspection metadata stays in the viewer; intermediate geometry is unchanged."""
    layers, histograms = [], []
    for layer in sorted(ordered, key=lambda l: l.layer.ref.row):
        ref, extent = layer.layer.ref, layer.z_extent()
        layers.append(dict(row=ref.row, name=layer.name, type=layer.type_name,
                           context=ref.context.value, polarity=ref.polarity.value,
                           form=ref.form.value, start_layer=ref.start_name, end_layer=ref.end_name,
                           z_mm=None if extent is None else dict(
                               start_mm=extent.start_mm, end_mm=extent.end_mm,
                               thickness_known=extent.thickness_known, source=extent.source)))
        features = layer.layer.features
        groups = features.histogram() if features is not None else []
        histogram = ['<p>输入图元统计（规整前；不代表输出材料面积）。'
                     '符号参数：MM 作用域用 µm，INCH 用 mil；输出坐标统一为 mm。</p>',
                     '<table><tr><th>图元</th><th>极性</th><th>符号</th><th>作用域</th><th>数量</th></tr>']
        for group in groups:
            histogram.append('<tr>' + ''.join(f'<td>{_escape(str(group[k]))}</td>'
                             for k in ('kind','polarity','symbol','symbol_units','count')) + '</tr>')
        histogram.append('</table>')
        histograms.append(f'<template id="hist-{ref.row}">{"".join(histogram)}</template>')
    return ''.join(histograms) + _matrix_panel(layers)


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
    view = dielectric_drill_cutouts(view)
    ordered = _ordered(list(view.layers), opts)
    body = _render_svg(view, opts, ordered, _viewbox(view, _render_digits(view, opts)))

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
<html lang="zh-CN">
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
      <button id="fit-layer" title="缩放到选中层">定位层</button>
      <button id="profilebtn" class="on" title="原始 step profile">板框</button>
      <button id="domainbtn" title="选中层的计算域（绿色虚线）">层域</button>
      <button id="matrixbtn">层叠矩阵</button>
      <button id="propertiesbtn" class="on" title="显示或隐藏选中层属性和输入图元统计">属性</button>
      <button id="measurebtn" title="依次点击两点；测量规整后的毫米坐标">测距</button>
    </div>
    <div id="searchbar"><input id="layer-search" type="search" placeholder="搜索层名或类型"
      aria-label="搜索图层"><div id="search-count">{len(ordered)} / {len(ordered)} 层 · 单击属性，双击隔离</div></div>
    <label id="sorter" for="sort-order">Layer order
      <select id="sort-order" aria-label="Layer order">
        <option value="type">By type</option>
        <option value="stackup">Top to bottom</option>
      </select>
    </label>
    <div id="layers">{_layer_rows(view, opts, ordered)}</div>
    <div id="overview"><svg xmlns="http://www.w3.org/2000/svg"
      viewBox="{' '.join(str(v) for v in _viewbox(view, _render_digits(view, opts)))}"
      aria-label="板框导航：点击移动视图"></svg></div>
  </div>
  <div id="stage">
    {body}
    <div id="info"></div>
    <div id="status">
      <span><b id="pos">&ndash;</b></span>
      <span id="measure-value"></span>
      <span>drag to pan &middot; wheel to zoom &middot; f fit &middot; w wireframe</span>
    </div>
  </div>
</div>
{_inspection_panels(ordered)}
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
) -> Outputs:
    """Write the preview, returning the paths produced.

    One resolution is chosen per artefact family and the geometry is
    re-regularised onto it before writing, so what the JSON reports, what the
    SVG draws and what the HTML lists agree at that resolution. Dielectric
    cutouts exist only in the drawing copy; JSON retains uncut geometry. By default both
    families follow the preview's own model resolution; an explicit
    ``preview_options``/``svg_options`` precision overrides it, and a mismatch
    between the two is honoured rather than hidden.
    """
    import os

    from .export import step_to_document

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
        from .intermediate import write_document
        write_document(step_to_document(job, json_view, round_digits=json_digits), path)
        outputs.json = path

    # The standalone SVG and the viewer contain exactly the same drawing.
    # Keep reuse local to this write: previews and rendering options can change
    # between calls, so a persistent render cache would be unsafe.
    if want_svg or want_html or per_layer_svg:
        draw_view = dielectric_drill_cutouts(draw_view)
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
