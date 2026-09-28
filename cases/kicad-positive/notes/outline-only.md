# 仅板框

- 造板入口：`generate.py --case outline-only`，KiCad 源板为
  `sources/outline-only.kicad_pcb`。
- 设计参数：20 × 10 mm 闭合矩形，KiCad 坐标 `(0,0)` 到 `(20,10)`；
  F.Cu、B.Cu 均无图元，不放焊盘、走线或孔。
- 几何预期：ODB++ Y 轴向上，step profile bbox 为
  `(0,-10,20,0)` mm，面积 200 mm²；介质实体为同一矩形。
- 原生审定：`misc/info` 为 v8.1、MM；matrix 有 2 铜层、1 介质层，
  无 DRILL。KiCad 强制出现的空 Margin / Courtyard 及 Edge.Cuts
  文档层保留在 `native/`，只在 `clean/` 删去。
