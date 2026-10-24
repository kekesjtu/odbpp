from __future__ import annotations
import argparse
import json
import sys
from typing import Optional, Sequence
from . import __version__
from .diagnostics import MATERIALS_DEFAULT_USED, VISUALIZE_FAILED, Diagnostics


def _run_to_stage(args: argparse.Namespace, diagnostics: Diagnostics) -> int:
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
    )

    # --- the intermediate document (the only interface the equivalence has) --
    try:
        from .intermediate import read_document
        document = read_document(args.path)
    except (OSError, ValueError) as exc:
        print(f"error: cannot read intermediate JSON {args.path}: {exc}", file=sys.stderr)
        return 2
    source_path = args.path
    step_name = document.steps[0].name

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
    map_path = os.path.join(out_dir, "materialmap.json")
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
        from pathlib import Path
        from .materialmap import read_document as read_map, restore_report_grid
        from .plotting.regions import plot_regions
        from .plotting.grids import plot_grid
        audit = restore_report_grid(json.loads(Path(report_path).read_text(encoding="utf-8")), read_map(map_path))
        if args.regions:
            plot_regions(document.internal_document(), Path(out_dir) / "regions", metadata=audit)
        if args.grids:
            plot_grid(document.internal_document(), Path(out_dir) / "grids", audit)

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
                gallery_path = os.path.join(out_dir, "index.html")
                write_gallery_html(
                    gallery_path,
                    f"{stem} · 等效物性图例",
                    entries,
                    note=_gallery_note(result, args),
                )
                print(f"wrote {gallery_path}")
                links = []
                if args.regions:
                    links.append('<a href="regions/regions.svg">Region geometry</a>')
                if args.grids:
                    links.append('<a href="grids/cartesian-grid.svg">Global Cartesian grid</a>')
                if links:
                    from pathlib import Path
                    page = Path(gallery_path)
                    page.write_text(page.read_text(encoding="utf-8").replace(
                        "</body>", "<p>" + " · ".join(links) + "</p></body>"), encoding="utf-8")
    except (VisualizeError, ValueError, OSError) as exc:
        # The MaterialMap JSON is the deliverable; failing to draw a picture
        # of them is a reported failure, not a silent one, and not a corrupt one.
        print(f"warning: could not render the visualisations: {exc}",
              file=sys.stderr)
        diagnostics.error(VISUALIZE_FAILED,
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


def run(args: argparse.Namespace, diagnostics: Diagnostics) -> int:
    """A failed computation or requested rendering leaves the previous run intact."""
    from pathlib import Path
    from .publication import OutputRun
    from .report import runtime_metrics
    import time
    start = time.perf_counter()
    directory = args.out or str(Path("out") / Path(args.path).stem)
    try:
        with OutputRun(directory) as output:
            staged_args = argparse.Namespace(**vars(args))
            staged_args.out = str(output.path)
            code = _run_to_stage(staged_args, diagnostics)
            if code:
                return code
            report = output.path / "diagnostics/run.json"
            audit = json.loads(report.read_text(encoding="utf-8"))
            audit["runtime"] = runtime_metrics(start)
            report.write_text(json.dumps(audit, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            output.commit()
    except (OSError, ValueError) as exc:
        print(f"error: cannot publish equivalence: {exc}", file=sys.stderr)
        return 2
    print(f"published equivalence outputs -> {directory}")
    return 0

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
