# 审阅问题修复（2026-09-22）

基线 `f7d6717`，修复报告 13 确认的三项问题，保留此前的导出性能优化。

## 修改

1. **HTML 图层复选框**：`_layer_rows` 补齐 div 起始标签的 `>`，使 `<input>` 成为真实子元素。补充 HTMLParser 结构测试，检查每层恰有一个 checkbox，且空层默认不选中，避免仅匹配字符串导致测试误通过。
2. **零位小数**：`_fmt` 仅在包含小数位时修剪尾零。现在 `10 / 100 / -20` 按原整数输出，负零仍统一为零。回归覆盖路径坐标和 SVG viewBox。
3. **step profile 输出拓扑**：profile 经与层几何相同的曲线离散化、环方向规整、目标网格量化和布尔规整后输出，保留显式 island/hole 标记。退化环通过 `export.degenerate_ring` 报告，诊断中明确指出 step profile；原始 profile 不被修改。测试覆盖窄条退化、窄缝闭合并集、孔洞及低精度导出后重新高精度输出。

`None` profile 仍输出 null，完全退化的 profile 输出空 polygons；JSON schema 未改变。此次 HTML 修复和 profile 规整会有意改变产物内容，不能继续要求所有文件与有缺陷的旧输出逐字节一致。

## 验证

- 针对性回归：8 passed（含 1 个既有空层测试）。
- 全量测试：**571 passed in 252.26 s**，无失败；本轮新增 7 项回归。
- 四例全部重新导出，保留 DOCUMENT 层及逐层 SVG，覆盖 122 个产物；所有总 SVG 和逐层 SVG 的长度与 SHA-256 均与修复前一致（默认四位精度）。
- 四例 JSON 的 step profile 共 8 个环，经 Shapely 独立检查均有效且面积大于零；窄缝/孔洞组合语义由合成回归进一步验证。
- 真实浏览器（Codex 内置浏览器）打开 minimal 样本：两个复选框正常初始化；none 后均未选中；单独选中 top 后 top 显示、bottom 隐藏；“+”将缩放改为 1.25，fit 恢复 `translate(0 0) scale(1)`；浏览器脚本错误日志为空。关闭临时标签页及本地 HTTP 服务。

同机、同一 `odbpp` conda 环境，本轮单次耗时如下。系统负载和文件缓存会影响数值，本表用于确认没有明显性能回退，不作为新增优化倍率。

| 样本 | 写出（s） | 解析 + 合成 + 写出（s） |
|---|---:|---:|
| minimal | 0.010 | 0.020 |
| kitchen-sink | 0.088 | 0.325 |
| video | 5.991 | 23.784 |
| rigidflex | 12.272 | 105.140 |

最终四例产物与计时保存在 `out/performance-optimized/`，修复前的指纹和计时保存在 `out/review-fixes-before.json`。沿用最终产物目录，不保留重复的旧导出。

复现：

```powershell
conda run --no-capture-output -n odbpp python tools/benchmark_export.py --output out/performance-optimized --per-layer-svg
conda run --no-capture-output -n odbpp python -m pytest -p no:cacheprovider --basetemp=build/review-fixes-full
```

提交：`5c79294` 修复预览；`3ba6cb3` 修复 step profile 输出。没有重写 JSON 编码器或几何合成算法；报告 13 的后续性能空间属于可继续优化的方向，不是本轮待修缺陷。
