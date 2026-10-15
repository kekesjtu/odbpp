"""Command line interface: ``python -m odbpp <command> PATH``.

Deliberately thin.  The commands exist to make the loader inspectable while the
geometry pipeline is being built, so they print structure and diagnostics rather
than producing final artwork.

Commands
--------
``info``       job root, units, ODB++ version, matrix summary
``layers``     layer inventory for a step, in matrix row order
``symbols``    referenced symbols, split into standard and user-defined
``diagnostics`` every warning/error collected while loading
``summarize``  all of the above, as text or JSON
``export``     compose the layers and write the mm JSON, SVG and HTML preview
``equivalent`` compose, equivalent every physical layer, write the JSON and draw
               the per-layer mask PNGs, heat maps and gallery
``heatmap``    draw kx/ky/kz heat maps from an already written MaterialMap.json

``summarize --format json`` is a *structural* dump -- file-level facts that make
the parse reviewable.  ``export --json`` is the intermediate format from the
AGENTS data flow: normalised, fully positive polygons in millimetres.  The two
are deliberately different documents, because one describes the input and the
other describes the geometry.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Optional, Sequence

from . import __version__
from .diagnostics import MATERIALS_DEFAULT_USED, VISUALIZE_FAILED, Diagnostics
from .errors import OdbError
from .geometry import DEFAULT_SAGITTA_MM, SUPPORTED_SYMBOL_PREFIXES, TessellationOptions
from .job import LoadOptions, load_job
from .model import Job, Layer, Step
from .symbols import parse_standard_symbol

__all__ = ["main", "build_parser"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="odbpp",
        description="Inspect an ODB++ v8.1 job (directory, .tgz or .zip).",
    )
    parser.add_argument("--version", action="version", version=f"odbpp {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("path", help="ODB++ directory, .tgz/.tar.gz or .zip")
        p.add_argument("--step", default=None,
                       help="step name to load (default: every step in the matrix)")
        p.add_argument("--no-features", action="store_true",
                       help="inventory only; skip parsing features files")

    p_info = sub.add_parser("info", help="job and matrix summary")
    common(p_info)

    p_layers = sub.add_parser("layers", help="layer inventory")
    common(p_layers)

    p_symbols = sub.add_parser("symbols", help="symbol inventory")
    common(p_symbols)

    p_diag = sub.add_parser("diagnostics", help="warnings and errors")
    common(p_diag)

    p_sum = sub.add_parser("summarize", help="everything at once")
    common(p_sum)
    p_sum.add_argument("--format", choices=("text", "json"), default="text")
    p_sum.add_argument("--json", dest="json_out", default=None,
                       help="write the JSON summary to this file")
    p_sum.add_argument("--max-records", type=int, default=5,
                       help="example feature records to show per layer (text only)")

    p_exp = sub.add_parser("export", help="compose layers; write JSON/SVG/HTML preview",)
    common(p_exp)
    p_exp.add_argument("-o", "--out", default=None,
                       help="output directory (default: out/<job name>)")
    p_exp.add_argument("--layer", action="append", default=None, metavar="NAME",
                       help="restrict to this layer; repeatable")
    p_exp.add_argument("--exclude-type", action="append", default=None,
                       metavar="TYPE",
                       help="skip layers of this matrix TYPE (e.g. DOCUMENT); repeatable")
    p_exp.add_argument("--sagitta", type=float, default=DEFAULT_SAGITTA_MM,
                       help=f"curve flattening tolerance in mm (default {DEFAULT_SAGITTA_MM})")
    p_exp.add_argument("--digits", type=int, default=4,
                       help="decimal places for coordinates (default 4, i.e. 0.1 um)")
    p_exp.add_argument("--minimum-stroke", type=float, default=0.0, metavar="MM",
                       help="draw zero-width artwork (r0 lines) at this width "
                            "(default 0, which keeps the geometry exact and "
                            "therefore invisible)")
    p_exp.add_argument("--only-json", action="store_true",
                       help="write only the intermediate JSON")
    p_exp.add_argument("--per-layer-svg", action="store_true",
                       help="also write one SVG per layer under <out>/layers/")
    p_exp.add_argument("--no-diagnostics", action="store_true",
                       help="omit the final parser diagnostic listing on stderr; exit status is unchanged")
    p_exp.set_defaults(json_out=None, format=None, max_records=5, no_features=False)

    p_eq = sub.add_parser(
        "equivalent",
        help="material equivalence: preview JSON -> MaterialMap.json",
        description="Equivalent each copper and dielectric layer to per-cell "
                    "kx/ky/kz, density and specific heat.",
    )
    p_eq.add_argument("path", nargs="?", default=None,
                      help="ODB++ directory, .tgz/.tar.gz or .zip")
    p_eq.add_argument("--from-json", dest="from_json", default=None,
                      metavar="FILE",
                      help="reuse the current intermediate JSON "
                           "instead of parsing an ODB++ input")
    p_eq.add_argument("--step", default=None, help="step name to parse")
    p_eq.add_argument("--no-features", action="store_true",
                      help="with an ODB++ input, inventory only (not useful here)")
    p_eq.add_argument("-o", "--out", default=None,
                      help="output directory (default: out/<job name>)")
    p_eq.add_argument("--px", type=int, default=None,
                      help="X raster pixels (default: dense geometry-aware sampling)")
    p_eq.add_argument("--py", type=int, default=None,
                      help="Y raster pixels (provide together with --px)")
    p_eq.add_argument("--grid-level", choices=("coarse","standard","fine"), default=None,
                      help="adaptive mesh preset (default: standard; expert sizes override)")
    p_eq.add_argument("--min-cell-mm", type=float, nargs="+", default=None, metavar="MM",
                      help="minimum cell edge, one value or X Y (default 0.1 mm)")
    p_eq.add_argument("--max-cell-mm", type=float, nargs="+", default=None, metavar="MM",
                      help="maximum cell edge, one value or X Y (default 5 mm)")
    p_eq.add_argument("--diagnostic-geometry-step-mm", dest="rectify_tolerance",
                      type=float, default=None, help=argparse.SUPPRESS)
    p_eq.add_argument("--diagnostic-area-error-ratio", dest="max_area_error_ratio",
                      type=float, default=0.05, help=argparse.SUPPRESS)
    p_eq.add_argument("--diagnostic-area-error-mm2", dest="max_area_error_mm2",
                      type=float, default=0.0, help=argparse.SUPPRESS)
    p_eq.add_argument("--materials", default=None, metavar="FILE",
                      help="JSON material configuration (global and per-layer "
                           "overrides)")
    p_eq.add_argument("--copper-k", type=float, default=None, metavar="W/MK")
    p_eq.add_argument("--copper-density", type=float, default=None, metavar="KG/M3")
    p_eq.add_argument("--copper-cp", type=float, default=None, metavar="J/KG/K")
    p_eq.add_argument("--base-k", type=float, default=None, metavar="W/MK")
    p_eq.add_argument("--base-density", type=float, default=None, metavar="KG/M3")
    p_eq.add_argument("--base-cp", type=float, default=None, metavar="J/KG/K")
    p_eq.add_argument("--digits", type=int, default=4,
                      help="preview coordinate resolution when parsing ODB++ "
                           "(default 4)")
    p_eq.add_argument("--no-raster-png", dest="raster_png",
                      action="store_false", default=True,
                      help="do not write the per-layer black/white raster PNGs "
                           "under <out>/masks/")
    p_eq.add_argument("--raster-scale", type=int, default=0, metavar="N",
                      help="isotropic nearest-neighbour zoom for the mask PNGs, "
                           "stretching a non-square board (default 0 = auto: "
                           "about 1400 px on the long side, board proportions "
                           "kept; 1 = exactly px x py)")
    p_eq.add_argument("--no-heatmap", dest="heatmap", action="store_false",
                      default=True,
                      help="do not write the per-layer kx/ky/kz heat maps "
                           "under <out>/heatmaps/")
    p_eq.add_argument("--heatmap-panel-width", type=int, default=1440,
                      metavar="PX",
                      help="width of one heat-map panel in pixels (default 1440; "
                           "height follows the board aspect ratio)")
    p_eq.add_argument("--heatmap-shared-scale", action="store_true",
                      help="use one colour scale for every layer instead of one "
                           "per layer")
    p_eq.add_argument("--heatmap-grid", action="store_true",
                      help="outline the JSON cells in the heat maps")
    p_eq.add_argument("--no-gallery", dest="gallery", action="store_false",
                      default=True,
                      help="do not write the per-run HTML gallery")
    p_eq.set_defaults(json_out=None, format=None, max_records=5)

    p_map = sub.add_parser(
        "heatmap",
        help="render per-layer kx/ky/kz heat maps from a MaterialMap JSON",
        description="Read a MaterialMap.json written by `equivalent` and draw one "
                    "PNG per layer with kx, ky and kz side by side. The JSON is "
                    "the interface: no ODB++ input is needed.",
    )
    p_map.add_argument("materialmap", help="MaterialMap.json written by `equivalent`")
    p_map.add_argument("-o", "--out", default=None,
                       help="output directory (default: <csv dir>/heatmaps)")
    p_map.add_argument("--layer", action="append", default=None, metavar="NAME",
                       help="only these layers (repeatable)")
    p_map.add_argument("--panel-width", type=int, default=1440, metavar="PX",
                       help="width of one panel in pixels (default 1440)")
    p_map.add_argument("--shared-scale", action="store_true",
                       help="use one colour scale for every layer")
    p_map.add_argument("--grid", action="store_true",
                       help="outline the JSON cells in the heat maps")
    p_map.add_argument("--no-gallery", dest="gallery", action="store_false",
                       default=True,
                       help="do not write the gallery HTML next to the PNGs")
    p_map.set_defaults(raster_png=False, heatmap=True)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    diagnostics = Diagnostics()

    if args.command == "equivalent":
        return _run_equivalent(args, diagnostics)
    if args.command == "heatmap":
        return _run_heatmap(args, diagnostics)

    options = LoadOptions(step=args.step, load_features=not args.no_features)

    try:
        job = load_job(args.path, options=options, diagnostics=diagnostics)
    except OdbError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.command == "export":
        return _run_export(job, args, diagnostics)

    if args.command == "info":
        _print_info(job)
    elif args.command == "layers":
        _print_layers(job)
    elif args.command == "symbols":
        _print_symbols(job)
    elif args.command == "diagnostics":
        _print_diagnostics(job)
    else:
        summary = summarize(job)
        if args.json_out:
            with open(args.json_out, "w", encoding="utf-8") as handle:
                json.dump(summary, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
        if args.json_out is None or args.format == "text":
            if args.format == "json":
                json.dump(summary, sys.stdout, indent=2, ensure_ascii=False)
                sys.stdout.write("\n")
            else:
                _print_summary(job, summary, max_records=args.max_records)
        elif args.json_out:
            print(f"wrote {args.json_out}")

    # Warnings are part of the result: a caller scripting this should be able to
    # tell "clean" from "loaded, but something was dropped" without parsing prose.
    return 1 if diagnostics.errors else 0


# ---------------------------------------------------------------------------
# structure extraction
# ---------------------------------------------------------------------------

def layer_summary(layer: Layer) -> dict[str, Any]:
    ref = layer.ref
    features = layer.features
    return {
        "name": layer.name,
        "dir": layer.dir_name,
        "row": ref.row,
        "context": ref.context.value,
        "type": ref.raw_type or None,
        "add_type": ref.add_type or None,
        "polarity": ref.polarity.value,
        "form": ref.form.value,
        "dielectric_type": ref.dielectric_type.value,
        "dielectric_name": ref.dielectric_name or None,
        "cu_top": ref.cu_top,
        "cu_bottom": ref.cu_bottom,
        "start_name": ref.start_name or None,
        "end_name": ref.end_name or None,
        "uid": ref.uid,
        "attributes": dict(layer.attributes),
        "feature_count": layer.feature_count,
        "declared_feature_count": features.declared_count if features else None,
        "feature_kinds": features.counts_by_kind() if features else {},
        "has_profile": layer.profile is not None,
        "profile_error": layer.profile_error,
        "tool_count": len(layer.tools.tools) if layer.tools else None,
        "records": ([r.raw.strip() for r in features.records] if features else []),
    }


def step_summary(step: Step) -> dict[str, Any]:
    header = step.header
    return {
        "name": step.name,
        "dir": step.dir_name,
        "units": header.units.system.value if header else None,
        "x_origin_mm": header.x_origin if header else None,
        "y_origin_mm": header.y_origin if header else None,
        "nested_step_repeats": len(header.repeats) if header else 0,
        "has_profile": step.profile is not None,
        "profile_polygons": len(step.profile.polygons) if step.profile else 0,
        "attributes": dict(step.attributes),
        "layers": [layer_summary(l) for l in step.layers],
    }


def summarize(job: Job) -> dict[str, Any]:
    """The full machine-readable summary."""
    return {
        "odbpp_version": __version__,
        "source": job.source_path,
        "job_root": job.root,
        "units": job.units.system.value,
        "units_declared": job.info.units_declared,
        "product": {
            "job_name": job.info.job_name,
            "version": job.info.version,
            "source": job.info.source,
            "creation_date": job.info.creation_date,
            "save_date": job.info.save_date,
            "save_app": job.info.save_app,
            "save_user": job.info.save_user,
        },
        "matrix": {
            "steps": [{"name": s.name, "col": s.col, "uid": s.uid} for s in job.matrix.steps],
            "layer_count": len(job.matrix.layers),
        },
        "orphan_step_dirs": list(job.orphan_step_dirs),
        "attribute_definitions": sorted(job.attributes.defs),
        "steps": [step_summary(s) for s in job.steps.values()],
        "diagnostics": [diag.__dict__ for diag in job.diagnostics.items]
        if job.diagnostics is not None else [],
    }


def symbol_summary(job: Job) -> dict[str, Any]:
    standard: dict[str, dict[str, Any]] = {}
    custom: dict[str, Any] = {}

    for step in job.steps.values():
        for layer in step.layers:
            features = layer.features
            if features is None:
                continue
            for ref in features.symbols:
                unit = ref.effective_unit(features.units.system)
                parsed = parse_standard_symbol(ref.name, unit)
                if parsed is not None:
                    entry = standard.setdefault(ref.name, {
                        "prefix": parsed.prefix,
                        "supported": parsed.prefix in SUPPORTED_SYMBOL_PREFIXES,
                        "params": parsed.params,
                        "unit": unit.value,
                        "uses": 0,
                    })
                    entry["uses"] += 1
                else:
                    entry = custom.setdefault(ref.name, {"uses": 0, "layers": []})
                    entry["uses"] += 1
                    if layer.name not in entry["layers"]:
                        entry["layers"].append(layer.name)

    return {
        "standard": dict(sorted(standard.items())),
        "custom": dict(sorted(custom.items())),
    }


# ---------------------------------------------------------------------------
# text rendering
# ---------------------------------------------------------------------------

def _print_info(job: Job) -> None:
    info = job.info
    print(f"root              {job.root!r}")
    print(f"units             {job.units.system.value}"
          f"{'' if info.units_declared else '  (defaulted, no UNITS in misc/info)'}")
    print(f"ODB++ version     {info.version}")
    print(f"job name          {info.job_name!r}")
    print(f"saved by          {info.save_app!r} / {info.save_user!r}")
    print(f"steps in matrix   {len(job.matrix.steps)}")
    print(f"layers in matrix  {len(job.matrix.layers)}")
    print(f"attribute defs    {len(job.attributes)}")
    if job.orphan_step_dirs:
        print(f"orphan step dirs  {', '.join(job.orphan_step_dirs)}")


def _print_layers(job: Job) -> None:
    for step in job.steps.values():
        print(f"step {step.name!r}  ({len(step.layers)} layers)")
        header = f"  {'layer':<24}{'ctx':<6}{'type':<15}{'pol':<5}{'n':>8}  profile"
        print(header)
        for layer in step.layers:
            profile = "layer" if layer.profile is not None else \
                      ("ERROR" if layer.profile_error else "-")
            print(f"  {layer.name:<24}{layer.ref.context.value:<6}"
                  f"{(layer.ref.raw_type or '?'):<15}{layer.ref.polarity.value:<5}"
                  f"{layer.feature_count:>8}  {profile}")


def _print_symbols(job: Job) -> None:
    summary = symbol_summary(job)
    print(f"standard symbols ({len(summary['standard'])})")
    print(f"  {'name':<40}{'prefix':<12}{'native':<8}params")
    for name, entry in summary["standard"].items():
        params = ", ".join(f"{k}={v:g}" for k, v in entry["params"].items())
        shown = name if len(name) <= 38 else name[:35] + "..."
        print(f"  {shown:<40}{entry['prefix']:<12}"
              f"{'yes' if entry['supported'] else 'no':<8}{params}")
    print(f"\nuser-defined symbols ({len(summary['custom'])})")
    for name, entry in summary["custom"].items():
        print(f"  {name:<40}uses={entry['uses']}")


def _print_diagnostics(job: Job) -> None:
    items = job.diagnostics.items if job.diagnostics is not None else []
    if not items:
        print("no diagnostics")
        return
    for diag in items:
        print(diag)


def _print_summary(job: Job, summary: dict[str, Any], *, max_records: int) -> None:
    _print_info(job)
    for step in summary["steps"]:
        print(f"\n=== step {step['name']!r} ===")
        if step["nested_step_repeats"]:
            print(f"  nested step repeats: {step['nested_step_repeats']} (ignored)")
        print(f"  profile: {'yes' if step['has_profile'] else 'no'}"
              f" ({step['profile_polygons']} polygons)")
        for layer in step["layers"]:
            kinds = ",".join(f"{k}:{v}" for k, v in sorted(layer["feature_kinds"].items()))
            print(f"  {layer['name']:<24}{layer['context']:<6}"
                  f"{(layer['type'] or '?'):<15}{layer['polarity']:<5}"
                  f"n={layer['feature_count']:<7} {kinds}")
            for raw in layer["records"][:max_records]:
                print(f"      | {raw}")

    symbols = symbol_summary(job)
    unsupported = [n for n, e in symbols["standard"].items() if not e["supported"]]
    print(f"\nsymbols: {len(symbols['standard'])} standard, "
          f"{len(symbols['custom'])} user-defined")
    if unsupported:
        print(f"  not expanded natively: {', '.join(sorted(unsupported))}")

    items = job.diagnostics.items if job.diagnostics is not None else []
    print(f"\ndiagnostics: {len(items)}")
    for diag in items:
        print(f"  {diag}")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------

def _run_export(job: Job, args: argparse.Namespace,
                diagnostics: Diagnostics) -> int:
    """Compose the job's step and write the preview artefacts."""
    import os

    from .export import PreviewOptions, build_step_preview, layer_summary_lines
    from .svg import SvgOptions, write_outputs

    if args.step is None and len(job.steps) != 1:
        print(f"error: job has {len(job.steps)} steps; choose one with --step",
              file=sys.stderr)
        for name in job.steps:
            print(f"  {name}", file=sys.stderr)
        return 2

    try:
        step = job.step(args.step) if args.step else job.single_step
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if step is None:
        print(f"error: no such step: {args.step!r}", file=sys.stderr)
        return 2

    preview_options = PreviewOptions(
        tessellation=TessellationOptions(sagitta_mm=args.sagitta),
        layers=args.layer,
        exclude_types=args.exclude_type or (),
        minimum_stroke_mm=args.minimum_stroke,
        round_digits=args.digits,
    )
    svg_options = SvgOptions(round_digits=args.digits)

    try:
        preview = build_step_preview(job, step, options=preview_options,
                                     diagnostics=diagnostics)
    except OdbError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    out_dir = args.out or os.path.join(
        "out", os.path.basename(job.source_path.rstrip("/\\")) or "job")

    if preview.failed_layers:
        print(f"warning: {len(preview.failed_layers)} layer(s) could not be "
              f"composed and are absent from the preview: "
              f"{', '.join(preview.failed_layers)}", file=sys.stderr)

    # A preview with no geometry at all is almost always a wrong --layer or
    # --step rather than a genuinely empty board, so say so instead of writing
    # files that look broken.
    if not preview.non_empty():
        print("error: every selected layer composed to nothing; nothing written",
              file=sys.stderr)
        for layer in preview.layers:
            print(f"  {layer.name}: {layer.geometry.records} records, "
                  f"{len(layer.rings)} rings", file=sys.stderr)
        return 2

    try:
        outputs = write_outputs(
            preview, job, out_dir,
            svg_options=svg_options,
            preview_options=preview_options,
            want_json=True,
            want_svg=not args.only_json,
            want_html=not args.only_json,
            per_layer_svg=args.per_layer_svg,
            stem=step.name,
        )
    except (OSError, ValueError) as exc:
        print(f"error: cannot export intermediate JSON: {exc}", file=sys.stderr)
        return 2

    print(f"step {step.name!r}: {len(preview.non_empty())} of {len(preview.layers)} "
          f"layers with geometry, {preview.total_polygons} rings, "
          f"input units {preview.units_in}, output mm")
    print(layer_summary_lines(preview))
    print()
    for path in outputs.all_paths():
        print(f"wrote {path} ({os.path.getsize(path) / 1e6:.2f} MB)")

    if args.no_diagnostics:
        return 1 if diagnostics.errors else 0

    errors = diagnostics.errors
    if errors:
        print(f"\n{len(errors)} error-level diagnostic(s):", file=sys.stderr)
        for item in errors:
            print(f"  {item}", file=sys.stderr)
    return 1 if errors else 0


# ---------------------------------------------------------------------------
# material equivalence (data-flow steps 4-5)
# ---------------------------------------------------------------------------

def _run_equivalent(args: argparse.Namespace, diagnostics: Diagnostics) -> int:
    """Write region MaterialMap JSON and an independent execution audit."""
    import json
    import os

    from .equiv import (
        EquivalenceError,
        EquivalenceOptions,
        equivalence_from_document,
        write_run_report,
    )
    from .grid import GridOptions
    from .materialmap import MaterialMapWriter
    from .materials import MaterialBook
    from .rectify import RectifyOptions
    from .visualize import (
        GalleryEntry,
        HeatmapOptions,
        MaskImage,
        VisualizeError,
        gallery_entries,
        render_heatmaps,
        unique_image_stem,
        write_gallery_html,
        write_layer_mask,
        z_text,
    )

    # --- the intermediate document (the only interface the equivalence has) --
    if args.from_json:
        try:
            from .intermediate import read_document
            document = read_document(args.from_json)
        except (OSError, ValueError) as exc:
            print(f"error: cannot read {args.from_json}: {exc}", file=sys.stderr)
            return 2
        source_path = args.from_json
        step_name = document.steps[0].name
    else:
        if not args.path:
            print("error: give an ODB++ input path or --from-json FILE",
                  file=sys.stderr)
            return 2
        from .export import PreviewOptions, build_step_preview, step_to_document

        try:
            job = load_job(args.path,
                           options=LoadOptions(step=args.step,
                                               load_features=not args.no_features),
                           diagnostics=diagnostics)
            step = job.step(args.step) if args.step else job.single_step
        except (OdbError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        if step is None:
            print(f"error: no such step: {args.step!r}", file=sys.stderr)
            return 2
        preview = build_step_preview(
            job, step,
            options=PreviewOptions(round_digits=args.digits),
            diagnostics=diagnostics,
        )
        try:
            document = step_to_document(job, preview, round_digits=args.digits)
        except ValueError as exc:
            print(f"error: invalid intermediate JSON: {exc}", file=sys.stderr)
            return 2
        source_path = job.source_path
        step_name = step.name

    # --- materials: file config, then command-line global overrides ----------
    config: dict = {}
    if args.materials:
        try:
            with open(args.materials, encoding="utf-8") as handle:
                loaded = json.load(handle)
        except (OSError, ValueError) as exc:
            print(f"error: cannot read {args.materials}: {exc}", file=sys.stderr)
            return 2
        if not isinstance(loaded, dict):
            print(f"error: {args.materials} must hold a JSON object",
                  file=sys.stderr)
            return 2
        config = dict(loaded)

    overrides: dict = {}
    for phase, prefix in (("copper", "copper"), ("base", "base")):
        entry = {}
        for field, attr in (("k_w_mk", f"{prefix}_k"),
                            ("density_kg_m3", f"{prefix}_density"),
                            ("specific_heat_j_kgk", f"{prefix}_cp")):
            value = getattr(args, attr)
            if value is not None:
                entry[field] = value
        if entry:
            overrides[phase] = entry
    if overrides:
        existing = config.get("global")
        merged = dict(existing) if isinstance(existing, dict) else {}
        for phase, entry in overrides.items():
            merged.setdefault(phase, {}).update(entry)
        config["global"] = merged

    if (args.px is None) != (args.py is None):
        print("--px and --py must be provided together", file=sys.stderr)
        return 2
    if args.grid_level and (args.min_cell_mm is not None or args.max_cell_mm is not None):
        print("--grid-level cannot be combined with expert cell sizes", file=sys.stderr)
        return 2
    try:
        options = EquivalenceOptions(
            grid=GridOptions(px=4096 if args.px is None else args.px, py=4096 if args.py is None else args.py,
                             min_cell_mm=args.min_cell_mm or .1, max_cell_mm=args.max_cell_mm or 5,
                             mesh_preset=args.grid_level or (None if args.min_cell_mm is not None or args.max_cell_mm is not None else "standard"),
                             auto_raster=args.px is None),
            materials=MaterialBook(config or None, diagnostics=diagnostics),
            rectify=RectifyOptions(
                tolerance_mm=args.rectify_tolerance,
                max_relative_area_error=args.max_area_error_ratio,
                max_absolute_area_error_mm2=args.max_area_error_mm2,
            ),

        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    out_dir = args.out or os.path.join(
        "out", os.path.basename(str(source_path).rstrip("/\\")) or "job")
    os.makedirs(out_dir, exist_ok=True)
    stem = step_name or "step"
    map_path = os.path.join(out_dir, f"{stem}.MaterialMap.json")
    report_path = os.path.join(out_dir, "diagnostics", "run.json")
    temp_path = map_path + ".tmp"

    # Raster masks are written while the layers are equivalented, so only one
    # mask is ever resident.  They go to a temporary name and are moved into
    # place only once the whole run is known to be trustworthy, exactly like the
    # JSON: a fatal run must not leave half a result behind.
    masks_dir = os.path.join(out_dir, "masks")
    mask_jobs: list[tuple[str, str, str]] = []
    mask_images: list[MaskImage] = []
    used_mask_stems: set[str] = set()

    def mask_sink(name: str, mask, pixel_step_mm) -> None:
        if not args.raster_png:
            return
        os.makedirs(masks_dir, exist_ok=True)
        final = os.path.join(
            masks_dir, f"{unique_image_stem(name, used_mask_stems)}.png")
        temp = final + ".tmp"
        mask_images.append(write_layer_mask(
            mask, temp, scale=args.raster_scale or None,
            pixel_step_mm=pixel_step_mm))
        mask_jobs.append((name, temp, final))

    # Stream rows as they are produced: a board-wide grid can hold millions of
    # cells and holding them all just to write a JSON is pure waste.
    count = 0
    try:
        with MaterialMapWriter(temp_path, step_name=step_name) as writer:
            result = equivalence_from_document(document, options,
                                               diagnostics=diagnostics,
                                               cell_sink=writer.add,
                                               mask_sink=mask_sink)
            count = writer.count
            if not result.fatal:
                writer.finish(result)
    except (EquivalenceError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        _discard(temp_path, [temp for _, temp, _ in mask_jobs])
        return 2

    if result.fatal:
        # An untrustworthy run must not look like a finished file.
        _discard(temp_path, [temp for _, temp, _ in mask_jobs])
        print("error: the equivalence run is not trustworthy; no output written",
              file=sys.stderr)
        for item in diagnostics.errors:
            print(f"  {item}", file=sys.stderr)
        return 2

    os.replace(temp_path, map_path)
    write_run_report(result, report_path, source_path=source_path,
                   step_name=step_name)
    _discard(os.path.join(out_dir, f"{stem}.MaterialMap.csv"),
             os.path.join(out_dir, f"{stem}.MaterialMap.meta.json"))
    mask_hrefs: dict[str, str] = {}
    for name, temp, final in mask_jobs:
        os.replace(temp, final)
        mask_hrefs[name] = f"masks/{os.path.basename(final)}"

    equivalent_layers = [r for r in result.layers if r.status == "equivalent"]
    print(f"step {step_name!r}: {count} equivalent cell(s) over "
          f"{len(equivalent_layers)} layer(s); raster {result.raster_px}x{result.raster_py}, "
          f"grid {result.grid.nx}x{result.grid.ny}, "
          f"{len(result.grid.cells)} board cell(s)")
    for report in result.layers:
        if report.status == "equivalent":
            print(f"  {report.name:<24}{report.type:<14} cells={report.cell_count}"
                  f"  z=[{report.z_start_mm:g},{report.z_end_mm:g}] mm"
                  f"{'' if report.thickness_known else '  (thickness unknown)'}")
        else:
            print(f"  {report.name:<24}{report.type:<14} {report.status}: "
                  f"{report.reason}")
    print(f"wrote {map_path} ({os.path.getsize(map_path) / 1e6:.2f} MB)")
    print(f"wrote {report_path} "
          f"({os.path.getsize(report_path) / 1e6:.2f} MB)")
    if mask_jobs:
        zooms = {image.isotropic_zoom for image in mask_images}
        if len(zooms) == 1 and None not in zooms:
            shape = f"zoom {zooms.pop():g} px per raster pixel"
        else:
            widest = max(image.width for image in mask_images)
            tallest = max(image.height for image in mask_images)
            shape = (f"up to {widest}x{tallest} px, board proportions kept")
        print(f"wrote {len(mask_jobs)} raster mask(s) -> {masks_dir} "
              f"({shape}; black=copper/drill, white=base, grey=no domain)")

    # --- pictures of what was written --------------------------------------
    # Rendered from the JSON file, not from the in-memory result: the picture is
    # then a picture of the delivered data, and the two cannot drift apart.
    images = []
    try:
        options_for_maps = HeatmapOptions(
            panel_width_px=args.heatmap_panel_width,
            shared_scale=args.heatmap_shared_scale,
            draw_grid=args.heatmap_grid,
        )
        if args.heatmap:
            report = render_heatmaps(map_path, os.path.join(out_dir, "heatmaps"),
                                     options=options_for_maps)
            images = report.images
            print(f"wrote {len(images)} heat map(s) -> "
                  f"{os.path.join(out_dir, 'heatmaps')}")
            if report.data.skipped_invalid:
                print(f"warning: {report.data.skipped_invalid} JSON row(s) were "
                      f"unusable and left blank in the heat maps",
                      file=sys.stderr)
        if args.gallery:
            entries = _gallery_entries(result, images, mask_hrefs, out_dir)
            if entries:
                gallery_path = os.path.join(out_dir, f"{stem}.images.html")
                write_gallery_html(
                    gallery_path,
                    f"{stem} · 等效物性图例",
                    entries,
                    note=_gallery_note(result, args),
                )
                print(f"wrote {gallery_path}")
    except (VisualizeError, ValueError, OSError) as exc:
        # The MaterialMap JSON is the deliverable; failing to draw a picture
        # of them is a reported failure, not a silent one, and not a corrupt one.
        print(f"warning: could not render the visualisations: {exc}",
              file=sys.stderr)
        diagnostics.warn(VISUALIZE_FAILED,
                         f"the run is written, but its pictures are not: {exc}")

    for item in diagnostics.warnings:
        if item.code == MATERIALS_DEFAULT_USED:
            print(item, file=sys.stderr)

    errors = diagnostics.errors
    if errors:
        print(f"\n{len(errors)} error-level diagnostic(s):", file=sys.stderr)
        for item in errors:
            print(f"  {item}", file=sys.stderr)
    return 1 if errors else 0


def _discard(*paths) -> None:
    """Remove temporary files, ignoring the ones that never appeared."""
    import os

    stack = list(paths)
    while stack:
        item = stack.pop()
        if isinstance(item, (list, tuple)):
            stack.extend(item)
            continue
        try:
            os.remove(item)
        except OSError:
            pass


def _gallery_entries(result, images, mask_hrefs: dict, gallery_dir: str) -> list:
    """One gallery entry per equivalented layer, with whatever was drawn."""
    import os

    from .visualize import GalleryEntry, format_z

    by_name = {image.layer: image for image in images}
    reports = [r for r in result.layers if r.status == "equivalent"]
    entries = []
    for position, report in enumerate(reports, start=1):
        image = by_name.get(report.name)
        heatmap = None
        k_text = ""
        if image is not None:
            heatmap = os.path.relpath(image.path, gallery_dir).replace(os.sep, "/")
            k_text = (f"k 范围 [{image.vmin:.4g}, {image.vmax:.4g}] W/(m·K)"
                      f"（kx/ky/kz 同一色标）")
        entries.append(GalleryEntry(
            name=report.name,
            index=position,
            count=len(reports),
            layer_type=report.type,
            z_text=format_z(report.z_start_mm, report.z_end_mm,
                            report.thickness_known),
            k_text=k_text,
            cells=report.cell_count,
            heatmap=heatmap,
            mask=mask_hrefs.get(report.name),
        ))
    return entries


def _gallery_note(result, args) -> str:
    """The provenance a picture cannot show: what was skipped and with what."""
    skipped = [r for r in result.layers if r.status != "equivalent"]
    parts = [f"raster {result.raster_px}x{result.raster_py}px",
             f"grid {result.grid.nx}x{result.grid.ny}",
             "介质层钻孔按实心铜计算，铜层仅依据铜图案"]
    if result.grid.size_constraints_mm:
        limits = result.grid.size_constraints_mm
        parts.append("单元边长 " + "，".join(
            f"{axis}=[{limits['min'][i]:g}, {limits['max'][i]:g}] mm"
            for i, axis in enumerate("XY")))
    if result.rectification.tolerance_mm is not None:
        parts.append(f"矩形化 tolerance={result.rectification.tolerance_mm:g} mm")
    if skipped:
        parts.append(f"{len(skipped)} 层未参与等效（逐层原因见 diagnostics/run.json）")
    if not args.raster_png:
        parts.append("本次未生成掩膜 PNG（--no-raster-png）")
    if not args.heatmap:
        parts.append("本次未生成热图（--no-heatmap）")
    elif args.heatmap_grid:
        parts.append("热图灰线标出 JSON 单元边界")
    return "；".join(parts) + "。掩膜灰阶：黑=铜（钻孔投影仅作用于介质），白=基底，灰=该层在此无计算域。"


def _run_heatmap(args: argparse.Namespace, diagnostics: Diagnostics) -> int:
    """Render kx/ky/kz heat maps from an already written MaterialMap.json."""
    import os

    from .visualize import (
        HeatmapOptions,
        VisualizeError,
        gallery_entries,
        render_heatmaps,
        write_gallery_html,
    )

    map_path = args.materialmap
    if not os.path.isfile(map_path):
        print(f"error: no such file: {map_path}", file=sys.stderr)
        return 2
    try:
        options = HeatmapOptions(panel_width_px=args.panel_width,
                                 shared_scale=args.shared_scale,
                                 draw_grid=args.grid)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    out_dir = args.out or os.path.join(
        os.path.dirname(os.path.abspath(map_path)), "heatmaps")
    try:
        report = render_heatmaps(map_path, out_dir, options=options,
                                 layers=args.layer)
        gallery_path = None
        if args.gallery:
            gallery_path = write_gallery_html(
                os.path.join(out_dir, "index.html"),
                f"{os.path.basename(map_path)} · kx/ky/kz 等效热导率热图",
                gallery_entries(report, gallery_dir=out_dir),
            )
    except (VisualizeError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    bounds = report.images[0]
    print(f"{map_path}: {len(report.images)} layer(s), "
          f"{report.data.row_count} JSON row(s), "
          f"grid {report.data.column_count}x{report.data.row_band_count} "
          f"(x=[{report.data.x_edges[0]:g},{report.data.x_edges[-1]:g}] mm)")
    for image in report.images:
        scale = (f"k=[{image.vmin:.4g},{image.vmax:.4g}] W/(m.K)"
                 if not options.shared_scale else "shared scale")
        print(f"  {image.index}/{image.count} {image.layer:<24}"
              f"cells={image.cells:<8}{scale}")
    if report.data.skipped_invalid:
        print(f"warning: {report.data.skipped_invalid} JSON row(s) were unusable "
              f"and left blank in the heat maps", file=sys.stderr)
    print(f"wrote {len(report.images)} heat map(s) -> {out_dir}")
    if gallery_path:
        print(f"wrote {gallery_path}")
    if bounds.vmax <= bounds.vmin:
        diagnostics.warn("visualize.flat_scale",
                         f"layer {bounds.layer!r} has a single conductivity "
                         f"value; its heat map is one flat colour")
    return 0
