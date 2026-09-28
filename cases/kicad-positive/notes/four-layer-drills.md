# 四铜层与贯穿、盲、埋孔

- 造板入口：`generate.py --case four-layer-drills`。
- 设计参数：20 × 10 mm 板框；F.Cu、In1.Cu、In2.Cu、B.Cu
  四铜层。三颗独立过孔的 KiCad 中心依次为 `(4,5)`、`(10,5)`、
  `(16,5)` mm；各铜环直径 0.8 mm，孔径 0.4 mm。
  跨度依次是 F.Cu→B.Cu 贯穿、F.Cu→In1.Cu 盲孔、
  In1.Cu→In2.Cu 埋孔。
- 几何预期：ODB++ Y 均为 `-5` mm；三个钻孔层各有一枚
  `r400` 投影。介质 1 扣贯穿和盲孔，介质 2 扣贯穿和埋孔，
  介质 3 只扣贯穿孔；对应孔洞 ring 数为 2、2、1，
  面积分别约 `200−2π·0.2²`、`200−2π·0.2²`、
  `200−π·0.2²` mm²（允许圆弧离散误差）。
- 原生审定：v8.1、MM；matrix 为 4 SIGNAL、3 DIELECTRIC、
  3 DRILL，钻孔 `START_NAME`/`END_NAME` 与上述跨度一致，
  各钻孔 `F 1`，tools 均写 400 µm。
