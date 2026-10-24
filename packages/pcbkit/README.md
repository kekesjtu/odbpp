# pcbkit

独立安装和运行的 ODB++ v8.1 解析器。支持目录、tar/tgz、zip；展开符号、处理极性与拓扑，输出毫米单位的正向实体多边形和逐层计算域。

```powershell
pcbkit export input.tgz -o result --sagitta-mm 0.001 --digits 4
pcbkit info input.tgz
```

`--sagitta-mm` 是圆弧离散的弦高误差（mm），`--digits` 是最终坐标小数位数。`--step` 选择单板 step；`--only-json` 跳过预览，`--per-layer-svg` 额外生成逐层图。

输出为 `intermediate.json`、`preview.svg`、自包含交互式 `preview.html`，以及 diagnostics 下的运行报告和生成文件清单。板内物理层保留绝对 Z、厚度来源和可信状态；介质 JSON 不扣 DRILL 投影。

退出码 0 表示成功，1 表示已发布带显式层失败状态的部分预览，2 表示直接失败。下游应在非零退出时停止。中间 JSON 模型随软件携带，无需安装 pcbeq；运行依赖仅为 Pydantic 和 pyclipper。
