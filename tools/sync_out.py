"""Keep one preview per ODB++ input and rebuild out/index.html.

Run through ``uv run --all-extras python tools/sync_out.py`` from the project
root. Existing valid previews are reused; pass ``--force`` to regenerate them.
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
    json_files = list(directory.glob("*.json")) if directory.is_dir() else []
    if len(json_files) != 1:
        return None
    try:
        data = json.loads(json_files[0].read_text(encoding="utf-8"))
        authored_source = str(source.relative_to(ROOT)).replace("\\", "/")
        output_source = data["source"]["path"].replace("\\", "/")
        steps = data["steps"]
        stem = json_files[0].stem
        if (data["schema"] != "odbpp/preview-1" or data["units"] != "mm"
                or output_source != authored_source or len(steps) != 1
                or not (directory / f"{stem}.svg").is_file()
                or not (directory / f"{stem}.html").is_file()):
            return None
        return json_files[0], data
    except (OSError, ValueError, KeyError, TypeError):
        return None


def render_index(rows: list[tuple[str, Path, Path, dict]]) -> str:
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
        "<p>每份输入保留 JSON、SVG 和可切换图层的 HTML。"
        "KiCad 原生 ZIP 与事后裁剪 ZIP 分组导出，可分别核对。</p>",
        "<table><tr><th>案例与输入</th><th>版本</th><th>层数</th><th>结果</th></tr>",
    ]
    for name, source, preview_dir, data in rows:
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
        lines.append(f"<tr><td>{escape(name)}<br><small>{escape(source_rel)}"
                     f"</small></td><td>{variant}</td><td>{layer_count}</td>"
                     f"<td>{links}</td></tr>")
    lines += ["</table>", "<p><small>运行 tools/sync_out.py 更新；"
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
    parser.add_argument("--force", action="store_true", help="regenerate existing previews")
    parser.add_argument("--prune", action="store_true",
                        help="remove out/ entries with no corresponding case")
    args = parser.parse_args()
    inputs = case_inputs()
    rows = []
    for name, source in inputs:
        output = OUT / name
        valid = None if args.force else existing_preview(output, source)
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
        rows.append((name, source, output, valid[1]))
    OUT.mkdir(exist_ok=True)
    (OUT / "index.html").write_text(render_index(rows), encoding="utf-8")
    if args.prune:
        prune_unmatched(inputs)
    print(f"index: {len(rows)} cases -> {OUT / 'index.html'}")


if __name__ == "__main__":
    main()
