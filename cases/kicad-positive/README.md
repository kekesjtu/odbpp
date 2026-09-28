# KiCad 原生 ODB++ v8.1 正向案例集

每块板只验证一个主要功能点。`sources/` 是可读的 KiCad 源板；`native/` 是
KiCad 10.0.6 CLI **未经修改**的 ZIP；`clean/` 是 `curate.py` 从原生 ZIP
事后裁剪的独立版本，不能称为原生导出。七个导出物均已核对 `misc/info`
为 ODB++ 8.1，且只有一个单板 step `PCB`。

| 案例 | 源板 | 原生 ZIP | 干净 ZIP | 独立预期说明 |
|---|---|---|---|---|
| 板框 | [outline-only](sources/outline-only.kicad_pcb) | [native](native/outline-only-mm.zip) | [clean](clean/outline-only-mm.zip) | [说明](notes/outline-only.md) |
| 四种焊盘 | [pad-shapes](sources/pad-shapes.kicad_pcb) | [native](native/pad-shapes-mm.zip) | [clean](clean/pad-shapes-mm.zip) | [说明](notes/pad-shapes.md) |
| 直线与圆弧 | [tracks-arcs](sources/tracks-arcs.kicad_pcb) | [native](native/tracks-arcs-mm.zip) | [clean](clean/tracks-arcs-mm.zip) | [说明](notes/tracks-arcs.md) |
| 双铜层贯穿孔 | [through-hole](sources/through-hole.kicad_pcb) | [mm](native/through-hole-mm.zip) / [in](native/through-hole-in.zip) | [mm](clean/through-hole-mm.zip) / [in](clean/through-hole-in.zip) | [说明](notes/through-hole.md) |
| 填充区、洞、旋转焊盘 | [zone-hole-rotation](sources/zone-hole-rotation.kicad_pcb) | [native](native/zone-hole-rotation-mm.zip) | [clean](clean/zone-hole-rotation-mm.zip) | [说明](notes/zone-hole-rotation.md) |
| 四铜层三种孔跨度 | [four-layer-drills](sources/four-layer-drills.kicad_pcb) | [native](native/four-layer-drills-mm.zip) | [clean](clean/four-layer-drills-mm.zip) | [说明](notes/four-layer-drills.md) |

## 复现

首次先在仓库根目录运行 `uv sync --all-extras`。造板脚本需使用 **KiCad
自带** Python 3.11 / `pcbnew` 10.0.6；导出及裁剪脚本使用项目的 uv 环境。
以下为本次 Windows 安装路径示例，可按本机路径替换：

```powershell
& 'E:\Program Files\KiCad\10.0\bin\python.exe' cases/kicad-positive/generate.py
# 单独重造一块板：generate.py --case pad-shapes
uv run --all-extras python cases/kicad-positive/export.py --kicad-cli 'E:\Program Files\KiCad\10.0\bin\kicad-cli.exe'
uv run --all-extras python cases/kicad-positive/curate.py
uv run --all-extras pytest tests/test_kicad_positive_cases.py
```

ZIP 中的时间戳、UUID 和图元顺序可能因 KiCad 版本或重新生成而变化；
复现时比较结构和几何语义，不比较整个 ZIP 的字节。`export.py` 不接触
`clean/`；`curate.py` 不修改 `native/`。裁剪仅保留 matrix 中的铜、介质、
钻孔层，去掉空组件层、重复于 step profile 的 Edge.Cuts 层、空的 Margin /
Courtyard 层和无关辅助目录。裁剪时严格检查删去层的图元数，所有保留
的 `features`、`attrlist`、`tools`、step `profile` 与原生包逐字节一致。

KiCad 10 保存时会强制保留空 Margin 和双面 Courtyard，因此原生包仍有
这些层；源板设置通过 `SetEnabledLayers` 只主动启用铜层与 Edge.Cuts。
焊盘只置于显式 `F.Cu`，封装参考和值文字隐藏。KiCad 默认在铜层
`attrlist` 写出 `.layer_dielectric`，这不是规范定义的铜层用途，
测试以 `.copper_weight` 判断铜厚，不把该字段当铜厚。

普通 pytest 只读固定 ZIP，无需本机安装 KiCad。负极性、层级 profile
和递归自定义符号等非 KiCad 导出语义仍由 `tests/fixtures/` 的手写夹具覆盖。
