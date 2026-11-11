"""Visualization consumes only the delivered MaterialMap, without the input board."""
from pathlib import Path

from .materialmap import read_document


def _geometry_images(document, directory, *, grids):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection
    from matplotlib.patches import Rectangle

    directory.mkdir(parents=True, exist_ok=True)
    layers = {}
    for region in document.regions:
        layers.setdefault(region.layer.name, []).append(region)
    profiles = {}
    for name, regions in layers.items():
        key = tuple(sorted((*r.bounds_mm.x, *r.bounds_mm.y,
                            r.grid_counts.x, r.grid_counts.y) for r in regions))
        profiles.setdefault(key, {"names": [], "regions": regions})["names"].append(name)
    columns = min(2, len(profiles))
    rows = (len(profiles) + columns - 1) // columns
    fig, axes = plt.subplots(rows, columns, figsize=(12, 7 * rows),
                             constrained_layout=True, squeeze=False)
    for axis, profile in zip(axes.flat, profiles.values()):
        segments = []
        for index, region in enumerate(profile["regions"]):
            x0, x1 = region.bounds_mm.x
            y0, y1 = region.bounds_mm.y
            axis.add_patch(Rectangle((x0, y0), x1 - x0, y1 - y0,
                                     facecolor="none" if grids else "#c5dbed",
                                     edgecolor="#264a6c", linewidth=.6))
            if grids:
                xs, ys = region.axis_lines("x"), region.axis_lines("y")
                segments.extend([[(x, y0), (x, y1)] for x in xs])
                segments.extend([[(x0, y), (x1, y)] for y in ys])
            elif len(profile["regions"]) <= 200:
                axis.text((x0 + x1) / 2, (y0 + y1) / 2, str(index),
                          ha="center", va="center", fontsize=6)
        if segments:
            axis.add_collection(LineCollection(segments, colors="#476783", linewidths=.3))
        axis.autoscale_view()
        axis.set_aspect("equal")
        axis.set_xlabel("X / mm")
        axis.set_ylabel("Y / mm")
        names = profile["names"]
        label = ", ".join(names[:3]) + (f" (+{len(names)-3} layers)" if len(names) > 3 else "")
        axis.set_title(label, fontsize=10)
    for axis in list(axes.flat)[len(profiles):]:
        axis.set_visible(False)
    fig.suptitle(f"{document.step or 'PCB'} · {'Region-local grids' if grids else 'MaterialMap regions'}")
    stem = "cartesian-grid" if grids else "regions"
    try:
        for suffix in ("png", "svg"):
            fig.savefig(directory / f"{stem}.{suffix}", dpi=160)
    finally:
        plt.close(fig)
    return directory / f"{stem}.png"


def render(source, output, **options):
    from .publication import OutputRun
    source = Path(source).resolve()
    output = Path(output)
    with OutputRun(output, manifest="diagnostics/image-artifacts.json") as publication:
        images = _render(source, publication.path, **options)
        relative = [image.relative_to(publication.path) for image in images]
        publication.commit()
    return [output / image for image in relative]


def _render(source, output, *, heatmap=True, regions=True, grids=True, gallery=True,
           panel_width=1440, shared_scale=False, draw_grid=False):
    """Render geometry, grids and properties entirely from MaterialMap."""
    from .visualize import HeatmapOptions, render_heatmaps
    from html import escape

    source, output = Path(source), Path(output)
    document = read_document(source)
    output.mkdir(parents=True, exist_ok=True)
    images = []
    if regions:
        images.append(_geometry_images(document, output / "regions", grids=False))
    if grids:
        images.append(_geometry_images(document, output / "grids", grids=True))
    if heatmap:
        report = render_heatmaps(str(source), str(output / "heatmaps"), options=HeatmapOptions(
            panel_width_px=panel_width, shared_scale=shared_scale, draw_grid=draw_grid))
        images.extend(Path(image.path) for image in report.images)
    if gallery:
        figures = "\n".join(f'<figure><img loading="lazy" src="{escape(image.relative_to(output).as_posix(), quote=True)}" '
                            f'style="max-width:100%"><figcaption>{escape(image.stem)}</figcaption></figure>' for image in images)
        (output / "index.html").write_text(
            '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>MaterialMap</title>'
            '<body style="max-width:1400px;margin:auto;font-family:sans-serif">'
            f'<h1>{escape(document.step or "PCB")} · MaterialMap</h1>{figures}</body></html>\n', encoding="utf-8")
    return images
