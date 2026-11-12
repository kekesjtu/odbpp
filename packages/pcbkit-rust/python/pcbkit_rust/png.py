"""Per-layer engineering images from final native regions."""
from pathlib import Path

from PIL import Image, ImageDraw


def write_layer_pngs(preview, out_dir, options, *, max_edge=1600):
    """Use a common board viewport, with Y upward and explicit island/hole fill.

    All layers, including empty layers, receive an image. Two-times sampling
    keeps narrow boundaries legible. Rendering-only dielectric cutouts are
    supplied by the same drawing view used for SVG and HTML.
    """
    from .svg import _ordered, layer_colour

    bounds = preview.bounds
    if bounds is None:
        raise ValueError("cannot render PNGs without board bounds")
    x0, y0, x1, y1 = bounds
    width, height = max(x1 - x0, 1e-6), max(y1 - y0, 1e-6)
    scale = (max_edge - 32) / max(width, height)
    size = (max(33, round(width * scale) + 32), max(33, round(height * scale) + 32))
    supersample = 2
    directory = Path(out_dir) / "layers"
    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    for layer in _ordered(preview.layers, options):
        mask = Image.new("L", (size[0] * supersample, size[1] * supersample))
        draw = ImageDraw.Draw(mask)
        # Final regions have disjoint boundaries. Descending size paints parents
        # before holes and nested islands, independently of ring enumeration.
        for ring in sorted(layer.rings, key=lambda ring: ring.area, reverse=True):
            points = [((16 + (x - x0) * scale) * supersample,
                       (16 + (y1 - y) * scale) * supersample) for x, y in ring.points]
            if len(points) >= 3:
                draw.polygon(points, fill=0 if ring.is_hole else 255)
        mask = mask.resize(size, Image.Resampling.LANCZOS)
        image = Image.new("RGB", size, "white")
        image.paste(layer_colour(layer), mask=mask)
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in layer.name)
        # Matrix rows guarantee unique names on case-insensitive filesystems.
        path = directory / f"{layer.layer.ref.row:03d}-{safe}.png"
        image.save(path)
        paths.append(str(path))
    return paths
