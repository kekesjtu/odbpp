# 15 · 移除中间 JSON 的 parse_info

中间 JSON 描述规整后的实体几何及必要物理语义，不承载原始图元解析审计。
本次删除每层 `parse_info` 及其四个字段：`polarity`、`base_from_profile`、
`feature_records`、`polarity_runs`。

解析器的内部 dataclass 仍保留极性、profile 基底使用状态及图元/极性段计数，
原有解析逻辑和内部统计不变。等效器不曾使用这些审计字段；实体多边形、岛/孔、
计算域、层类型、Z、厚度来源和可信状态、钻孔跨度及解析失败状态仍通过接口传递。

`src/odbpp/export.py` 的层导出不再构造审计字典；
`src/odbpp/intermediate.py` 删除 `ParseInfoModel` 和 `LayerModel.parse_info`，
从同一 Pydantic 模型重新生成 `docs/intermediate.schema`。
当前格式保持严格字段校验，不添加旧字段兼容分支；案例输出直接从源 ODB++ 重新生成。

导出与真实 KiCad 案例测试改为确认 JSON 不包含该字段，同时继续从内部模型验证原始极性。
共用模型 round-trip、几何/等效结果和介质 DRILL 绘图副本测试继续验证接口功能。

## 复现

```powershell
uv sync --all-extras
uv run --all-extras python -m pytest tests/test_export.py tests/test_intermediate.py tests/test_kicad_positive_cases.py tests/test_drill_policy.py
uv run --all-extras python tools/sync_out.py --grid-level standard --heatmap-grid
uv run --all-extras python tools/validate_architecture.py --audit-out --output docs/equivalence/output-validation.json
```

相关测试：106 passed（126.30 s）。

19 个案例已从源 ODB++ 重导出，共删除 248 个层上的 parse_info。
全部新 JSON 通过当前 Pydantic 导入校验，所有层均无 parse_info。
对删除前的 JSON 仅排除该字段后比较完整 step 数据（包括图案、域、层目录、Z 和厚度来源），
加上单位和坐标分辨率，19 个案例的 SHA-256 均完全相同；所有 MaterialMap CSV 字节也完全相同。
对照记录见 `docs/equivalence/remove-parse-info-validation.json`。

最终逐单元核查通过：19 个案例、100 个物理层、386,938 行 MaterialMap。
SVG/HTML、掩膜、热图及几何诊断随案例更新。
先前 JSON 精简与 DRILL 政策的历史验证记录保留，不覆盖其测量证据。
