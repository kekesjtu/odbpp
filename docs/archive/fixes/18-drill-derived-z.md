# 18 · DRILL 深度由铜层端点推导

MaterialMap JSON 迁移完成后，继续精简中间 JSON 的钻孔记录：铜层保留自身 `z_mm`，
DRILL 不再重复导出该字段。DRILL 的几何/深度由正向投影多边形和 `copper_span` 表达；
层名、类型、解析状态等目录字段仍用于识别。

## 数据与消费行为

- `copper_span=[a,b]` 引用同一层目录中 `copper_index=a/b` 的铜层。
- 需要绝对范围时，在消费者内部取起点铜层 `z_mm.start_mm` 与终点铜层 `z_mm.end_mm`。
- 介质覆盖仍优先按铜层拓扑跨度判断。未知厚度可能造成 best-effort Z 重合，不能据此扩大盲孔范围。
- 介质自身缺少拓扑跨度时，仍可用上述推导范围判断相交；其厚度未知时保留可定位警告。
- 需要介质采样时，钻孔跨度未知、端点铜层缺少 Z 或钻孔几何不可用会阻止正式结果。
- 仅铜层输入仍不要求解算钻孔跨度，所有铜层图案和材料采样完全忽略 DRILL。
- Parser 内部 Stackup 继续按需提供钻孔 Z，用于 SVG/HTML 预览。删除的是中间接口的重复数据。

共享 Pydantic 模型拒绝 DRILL 上的非空 `z_mm`；项目保持唯一当前格式，不添加迁移或版本分支。
文本 schema、字段参考、钻孔采样说明和解析器开发指南已同步。
开发指南中的完整注释源码与实际 `intermediate.py` 经 AST 对照一致，合成 JSON 示例可通过模型校验。

## 验证

测试覆盖：导出无重复 Z、拒绝输入旧 DRILL Z、按铜层索引而非目录位置查找端点、
起点铜层上表面到终点铜层下表面的范围推导、两个端点分别缺少 Z 的错误、
盲孔拓扑、未知厚度和介质钻孔覆盖。MaterialMap 的五项物性、行主序及热图读取保持原有验证。

全量测试通过：800 passed，175.85 s。19 个案例全部同步，26 个 DRILL 层均不导出 `z_mm`，端点铜层 Z 保留。
逐文件对照修改前 MaterialMap 的 SHA-256，19 份文件逐字节完全一致，包括几何网格和全部物性。
逐案例记录见 [drill-z-validation.json](../../validation/reports/drill-z-validation.json)。
最终逐单元域归属、全局轴线、尺寸、采样容量及物性范围核查也全部通过，见 [output-validation.json](../../validation/reports/output-validation.json)。

```powershell
uv sync --all-extras
uv run --all-extras python -m pytest
uv run --all-extras python tools/sync_out.py --grid-level standard --heatmap-grid
uv run --all-extras python tools/validate_architecture.py --audit-out
```

旧 CSV 性能验证工具仅为其隔离历史读取器的测试输入补回可推导 Z；正式接口和运行路径没有兼容旧格式的代码。
