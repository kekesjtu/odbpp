"""Keep previews and material maps for every case together under out/.

Run through ``uv run --all-extras python tools/sync_out.py --grid-level standard`` from the project
root. Existing valid outputs are reused; pass ``--force`` to regenerate them.
KiCad native and curated clean ZIPs have separate preview directories, so the
source of every output remains visible and the two versions can be compared.
"""

from __future__ import annotations

import argparse
from html import escape
import json
from pathlib import Path
import shutil
import subprocess
import sys

from odbpp.visualize import unique_image_stem
from odbpp.grid import GridOptions
from odbpp.equiv import pipeline_fingerprint


ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "cases"
OUT = ROOT / "out"


def case_inputs() -> list[tuple[str, Path]]:
    found: list[tuple[str, Path]] = []
    for item in sorted(CASES.iterdir()):
        if item.is_dir() and (item / "misc" / "info").is_file():
            found.append((item.name, item))
        elif item.is_file() and item.suffix.lower() in {".zip", ".tgz"}:
            found.append((item.name, item))
    for variant in ("native", "clean"):
        for item in sorted((CASES / "kicad-positive" / variant).glob("*.zip")):
            found.append((f"kicad-positive/{variant}/{item.stem}", item))
    if not found:
        raise RuntimeError("no ODB++ cases found")
    return found


def existing_preview(directory: Path, source: Path) -> tuple[Path, dict] | None:
    json_files = ([p for p in directory.glob("*.json")
                   if not p.name.endswith(".MaterialMap.meta.json")]
                  if directory.is_dir() else [])
    if len(json_files) != 1:
        return None
    try:
        data = json.loads(json_files[0].read_text(encoding="utf-8"))
        authored_source = str(source.relative_to(ROOT)).replace("\\", "/")
        output_source = data["source"]["path"].replace("\\", "/")
        steps = data["steps"]
        stem = json_files[0].stem
        if (data["schema"] != "odbpp/preview-2"
                or data["units"] != "mm"
                or output_source != authored_source or len(steps) != 1
                or not (directory / f"{stem}.svg").is_file()
                or not (directory / f"{stem}.html").is_file()):
            return None
        return json_files[0], data
    except (OSError, ValueError, KeyError, TypeError):
        return None


def image_names(layers: list[str]) -> set[str]:
    used: set[str] = set()
    return {f"{unique_image_stem(name, used)}.png" for name in layers}


def existing_equivalence(directory: Path, preview: Path, *,
                         px: int | None, py: int | None, min_cell_mm=0.1, max_cell_mm=5.0,
                         grid_level: str | None = None,
                         draw_grid: bool = False) -> dict | None:
    """Reuse only a complete result for this JSON and this raster/grid choice."""
    stem = preview.stem
    csv = directory / f"{stem}.MaterialMap.csv"
    meta = directory / f"{stem}.MaterialMap.meta.json"
    gallery = directory / f"{stem}.images.html"
    if not all(p.is_file() and p.stat().st_size > 0 for p in (csv, meta, gallery)):
        return None
    if csv.stat().st_mtime_ns < preview.stat().st_mtime_ns:
        return None
    try:
        if ("热图灰线标出 CSV 单元边界" in gallery.read_text(encoding="utf-8")) != draw_grid:
            return None
        data = json.loads(meta.read_text(encoding="utf-8"))
        expected_source = preview.relative_to(ROOT).as_posix()
        actual_source = data["source"]["path"].replace("\\", "/")
        config=data["grid"].get("mesh_configuration") or {}
        if config.get("mode") != ("preset" if grid_level else "expert"):
            return None
        if grid_level and config.get("preset")!=grid_level:
            return None
        if (config.get("raster") or {}).get("mode") != ("automatic" if px is None else "explicit"):
            return None
        if (data["schema"] != "odbpp/materialmap-5"
                or data.get("pipeline_fingerprint") != pipeline_fingerprint()
                or data["source"]["preview_schema"] != "odbpp/preview-2"
                or actual_source != expected_source
                or (px is not None and (data["raster"]["px"] != px or data["raster"]["py"] != py))
                or (not grid_level and data["grid"]["size_constraints_mm"] != {
                    "min": list(GridOptions(px or 4096, py or 4096, min_cell_mm, max_cell_mm).min_cell_mm),
                    "max": list(GridOptions(px or 4096, py or 4096, min_cell_mm, max_cell_mm).max_cell_mm)})
                or not data["rectification"]["ok"]
                or any(d["level"] == "error" for d in data["diagnostics"])):
            return None
        layers = [r["name"] for r in data["layers"]
                  if r["status"] == "equivalent"]
        if not layers:
            return None
        names = image_names(layers)
        for kind in ("masks", "heatmaps"):
            folder = directory / kind
            if (not folder.is_dir() or folder.is_symlink()
                    or {p.name for p in folder.glob("*.png")} != names
                    or any(not (folder / name).is_file()
                           or (folder / name).stat().st_size == 0
                           for name in names)):
                return None
        return data
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def prune_stale_images(directory: Path, layers: list[str]) -> None:
    """Remove obsolete generated PNGs after a replacement run succeeded."""
    expected = image_names(layers)
    root = OUT.resolve()
    for kind in ("masks", "heatmaps"):
        folder = directory / kind
        if folder.is_symlink() or not folder.resolve().is_relative_to(root):
            raise RuntimeError(f"refusing to clean images outside out/: {folder}")
        for path in folder.glob("*.png"):
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                raise RuntimeError(f"refusing to clean image outside out/: {path}")
            if path.name not in expected:
                print(f"prune  {path.relative_to(ROOT)}", flush=True)
                path.unlink()


def render_index(rows: list[tuple[str, Path, Path, dict, dict]]) -> str:
    lines = [
        "<!doctype html>", '<html lang="zh-CN">', '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        "<title>ODB++ 案例导出</title>",
        "<style>body{max-width:1050px;margin:48px auto;padding:0 22px;"
        "font:16px/1.6 system-ui;color:#17253b;background:#f7f9fc}"
        "table{border-collapse:collapse;width:100%;background:white}"
        "td,th{padding:10px;text-align:left;border-bottom:1px solid #dde4ef}"
        "a{color:#135baf}small{color:#465569}</style>",
        "<h1>ODB++ 案例导出</h1>",
        "<p>每份输入保留 JSON、SVG、可切换图层的 HTML，以及物性等效 CSV、"
        "逐层光栅掩膜和 kx/ky/kz 热图。"
        "KiCad 原生 ZIP 与事后裁剪 ZIP 分组导出，可分别核对。</p>",
        "<table><tr><th>案例与输入</th><th>版本</th><th>层数</th>"
        "<th>等效层</th><th>XY 网格</th><th>结果</th></tr>",
    ]
    for name, source, preview_dir, data, equivalent in rows:
        relative = preview_dir.relative_to(OUT).as_posix()
        stem = data["steps"][0]["name"]
        layer_count = len(data["steps"][0]["layers"])
        source_rel = source.relative_to(ROOT).as_posix()
        variant = ("KiCad 原生（未修改）" if "/native/" in source_rel else
                   "事后裁剪（非原生）" if "/clean/" in source_rel else "既有样本")
        links = " · ".join(
            f'<a href="{escape(relative)}/{escape(stem)}.{ext}">{label}</a>'
            for ext, label in (("html", "预览"), ("json", "JSON"), ("svg", "SVG"))
        )
        links += " · " + " · ".join(
            f'<a href="{escape(relative)}/{escape(stem)}.{suffix}">{label}</a>'
            for suffix, label in (("MaterialMap.csv", "CSV"),
                                  ("MaterialMap.meta.json", "meta"),
                                  ("images.html", "掩膜/热图"))
        )
        region_plot = preview_dir / "regions" / "regions.svg"
        if region_plot.is_file():
            links += (f' · <a href="{escape(relative)}/regions/regions.svg">region 分区</a>')
        if (preview_dir / "grid" / "cartesian-grid.svg").is_file():
            links += (f' · <a href="{escape(relative)}/grid/cartesian-grid.svg">全局笛卡尔栅</a>')
        equivalent_count = sum(r["status"] == "equivalent"
                               for r in equivalent["layers"])
        lines.append(f"<tr><td>{escape(name)}<br><small>{escape(source_rel)}"
                     f"</small></td><td>{variant}</td><td>{layer_count}</td>"
                     f"<td>{equivalent_count}</td>"
                     f"<td>{equivalent['grid']['nx']}×{equivalent['grid']['ny']}</td>"
                     f"<td>{links}</td></tr>")
    lines += ["</table>", "<p><small>光栅与尺寸约束以各 case 的 meta 为准；"
              "表中 nx/ny 是全局笛卡尔栅的轴向区间数，完整实体单元见 CSV。"
              "修改解析器后使用 --force 重新生成。</small></p>", "</html>"]
    return "\n".join(lines) + "\n"


def prune_unmatched(inputs: list[tuple[str, Path]]) -> None:
    """Remove outputs with no case, guarding every resolved target path."""
    root = OUT.resolve()
    expected = {name for name, _ in inputs}
    keep_top = {name.split("/", 1)[0] for name in expected} | {"index.html"}
    candidates = [p for p in OUT.iterdir() if p.name not in keep_top]
    suite = OUT / "kicad-positive"
    if suite.is_dir():
        variants = {"native", "clean"}
        candidates += [p for p in suite.iterdir() if p.name not in variants]
        for variant in variants:
            group = suite / variant
            if group.is_dir():
                candidates += [p for p in group.iterdir()
                               if f"kicad-positive/{variant}/{p.name}" not in expected]
    for path in candidates:
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise RuntimeError(f"refusing to remove path outside out/: {path}")
        print(f"prune  {path.relative_to(ROOT)}", flush=True)
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true",
                        help="regenerate previews and equivalence outputs")
    parser.add_argument("--force-equivalent", action="store_true",
                        help="regenerate equivalence outputs but reuse valid previews")
    parser.add_argument("--px", type=int, default=None,
                        help="X raster pixels per case (default: dense automatic)")
    parser.add_argument("--py", type=int, default=None,
                        help="Y raster pixels per case (provide with --px)")
    parser.add_argument("--grid-level", choices=("coarse","standard","fine"), default=None,
                        help="adaptive grid preset (default standard; expert sizes override)")
    parser.add_argument("--min-cell-mm", type=float, nargs="+", default=None, metavar="MM",
                        help="minimum edge, one value or X Y (default 0.1 mm)")
    parser.add_argument("--max-cell-mm", type=float, nargs="+", default=None, metavar="MM",
                        help="maximum edge, one value or X Y (default 5 mm)")
    parser.add_argument("--heatmap-grid", action="store_true",
                        help="outline each CSV cell in heatmaps")
    parser.add_argument("--prune", action="store_true",
                        help="remove out/ entries with no corresponding case")
    args = parser.parse_args()
    if (args.px is None)!=(args.py is None):
        parser.error("--px and --py must be provided together")
    expert=args.min_cell_mm is not None or args.max_cell_mm is not None
    if expert and args.grid_level:
        parser.error("--grid-level cannot be combined with expert cell sizes")
    grid_level=args.grid_level or (None if expert else "standard")
    try:
        sizes = GridOptions(4096 if args.px is None else args.px,
                            4096 if args.py is None else args.py,
                            args.min_cell_mm or .1,args.max_cell_mm or 5)
    except ValueError as exc:
        parser.error(str(exc))
    draw_grid = args.heatmap_grid
    inputs = case_inputs()
    rows = []
    OUT.mkdir(exist_ok=True)
    for name, source in inputs:
        output = OUT / name
        valid = None if args.force else existing_preview(output, source)
        preview_regenerated = valid is None
        if valid is None:
            command = [sys.executable, "-m", "odbpp", "export",
                       str(source.relative_to(ROOT)), "-o",
                       str(output.relative_to(ROOT))]
            print(f"export {name}", flush=True)
            subprocess.run(command, cwd=ROOT, check=True)
            valid = existing_preview(output, source)
            if valid is None:
                raise RuntimeError(f"export was not complete: {output}")
        else:
            print(f"reuse  {name}", flush=True)
        preview = valid[0]
        equivalent = (None if args.force or args.force_equivalent
                      or preview_regenerated else
                      existing_equivalence(output, preview, px=args.px,
                                           py=args.py, min_cell_mm=sizes.min_cell_mm,
                                           max_cell_mm=sizes.max_cell_mm,
                                           draw_grid=draw_grid,grid_level=grid_level))
        if equivalent is None:
            command = [sys.executable, "-m", "odbpp", "equivalent",
                       "--from-json", preview.relative_to(ROOT).as_posix(),
                       "-o", output.relative_to(ROOT).as_posix()]
            if args.px is not None:
                command.extend(["--px",str(args.px),"--py",str(args.py)])
            if grid_level:
                command.extend(["--grid-level",grid_level])
            else:
                command.extend(["--min-cell-mm",*map(str,sizes.min_cell_mm),
                                "--max-cell-mm",*map(str,sizes.max_cell_mm)])
            if draw_grid:
                command.append("--heatmap-grid")
            print(f"equiv  {name}", flush=True)
            subprocess.run(command, cwd=ROOT, check=True)
            meta = json.loads((output / f"{preview.stem}.MaterialMap.meta.json")
                              .read_text(encoding="utf-8"))
            prune_stale_images(output, [r["name"] for r in meta["layers"]
                                        if r["status"] == "equivalent"])
            equivalent = existing_equivalence(output, preview, px=args.px,
                                               py=args.py, min_cell_mm=sizes.min_cell_mm,
                                               max_cell_mm=sizes.max_cell_mm,
                                               draw_grid=draw_grid,grid_level=grid_level)
            if equivalent is None:
                raise RuntimeError(f"equivalence was not complete: {output}")
        else:
            print(f"reuse  {name} equivalence", flush=True)
        try:
            from tools.plot_regions import plot_regions
            from tools.plot_grid import plot_grid
        except ModuleNotFoundError:
            from plot_regions import plot_regions
            from plot_grid import plot_grid
        plot_regions(valid[1], output / "regions", px=equivalent["raster"]["px"],py=equivalent["raster"]["py"],metadata=equivalent)
        plot_grid(valid[1], output / "grid", equivalent)
        rows.append((name, source, output, valid[1], equivalent))
    (OUT / "index.html").write_text(render_index(rows), encoding="utf-8")
    if args.prune:
        prune_unmatched(inputs)
    print(f"index: {len(rows)} cases -> {OUT / 'index.html'}")


if __name__ == "__main__":
    main()
