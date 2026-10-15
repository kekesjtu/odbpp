# 16 · 中间 JSON 不导出根 diagnostics

用户要求取消中间 JSON 根节点的 diagnostics。当前格式仅保留实体几何及必要物理语义，解析诊断留在内部模型、CLI 或独立报告中。

## 修改

- 删除 `BoardDocument.diagnostics` 和仅供它使用的 `DiagnosticModel`，重生成 `docs/intermediate.schema`。
- `step_to_document()` / `step_to_json()` 不再构造根诊断列表，删除 `include_diagnostics` 参数与 `_diagnostic_json()` 辅助函数。
- `write_outputs()` 删除同名参数，所有 JSON 输出路径始终遵守同一格式。
- CLI `--no-diagnostics` 仅控制最后的终端诊断列表；帮助文字同步调整。它不影响 JSON 内容，也不改变失败退出码。
- 内部 `Diagnostics`、CLI 的专用诊断 / info 输出、MaterialMap.meta.json 的等效报告保留。后两类报告不属于 Parser 与等效器之间的中间 JSON。
- 逐层 `geometry_status` / `geometry_error` 保留，错误层不会因为根诊断被移除而变成成功的空图案。
- 当前接口参考、开发者指南及其完整注释代码已同步；附录与当前源码 AST、模型 schema 一致，完整 JSON 示例仍可导入。

测试覆盖默认导出、内部诊断可用、坐标退化警告及去重、CLI 失败状态和诊断列表控制、公共 SVG/HTML/JSON 写入路径、共享模型与 schema。

## 复现

```powershell
uv sync --all-extras
uv run --all-extras python -m pytest tests/test_export.py tests/test_intermediate.py tests/test_cli.py tests/test_svg.py tests/test_drill_policy.py
uv run --all-extras python tools/sync_out.py --grid-level standard --heatmap-grid
uv run --all-extras python tools/validate_architecture.py --audit-out --output docs/equivalence/output-validation.json
```

相关测试：188 passed（171.80 s）。

19 个原有案例已从源 ODB++ 重新导出，根节点均无 diagnostics，全部通过当前 Pydantic 模型导入校验。
共移除 226 条原先写在中间 JSON 根节点的诊断；这些诊断仍可由内部模型或专用诊断入口取得。
逐案例比较完整 step 数据（含图案、计算域、层目录、Z、厚度来源与逐层失败状态）及单位、坐标分辨率，
SHA-256 全部一致；所有 MaterialMap CSV 字节也完全一致。
对照记录见 `docs/equivalence/remove-root-diagnostics-validation.json`。

最终核查通过：19 个案例、100 个物理层、386,938 行 MaterialMap。
SVG/HTML、掩膜、热图及几何诊断随案例更新。
先前修复文档和验证报告作为历史记录保留，当前接口以更新后的指南和模型为准。
