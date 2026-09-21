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
from .diagnostics import Diagnostics
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
                       help="omit the diagnostics array from the JSON")
    p_exp.set_defaults(json_out=None, format=None, max_records=5, no_features=False)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    diagnostics = Diagnostics()
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

    outputs = write_outputs(
        preview, job, out_dir,
        svg_options=svg_options,
        preview_options=preview_options,
        want_json=True,
        want_svg=not args.only_json,
        want_html=not args.only_json,
        per_layer_svg=args.per_layer_svg,
        stem=step.name,
        # The flag has to reach the serialiser, not just the terminal report:
        # omitting the array from the file is what the option promises.
        include_diagnostics=not args.no_diagnostics,
    )

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
