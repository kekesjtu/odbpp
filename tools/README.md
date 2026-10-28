# 维护工具

从仓库根目录使用项目 uv 环境：

```powershell
uv sync --all-extras
uv run --all-extras python tools/<脚本> --help
```

| 工具 | 用途 | 输入与输出 |
| --- | --- | --- |
| `run_pipeline.py` | 通过两个子进程串联 CLI，可指定独立环境入口 | ODB++ → pcbkit / pcbeq 结果及 pipeline 日志与耗时 |
| `sync_out.py` | 批量维护案例与总索引，按配置和指纹复用，支持 `--force`、`--force-equivalent` | 本地 cases → out |
| `sync_contract.py` | 从模型权威源同步包内模型与 schema，`--check` 检查一致性 | contracts → 两产品模型、contracts 与 docs/interfaces 下的 schema |
| `verify_distributions.py` | 构建 wheel / sdist、独立安装、跨环境交接；缓存可用时支持 `--offline` | 两产品与本地样本 → tmp/distribution-check |
| `benchmark_pipeline.py` | 记录完整 CLI 时间、进程峰值、region / 网格数和数值哈希，支持重复与基线比较 | `--case` → `--output` 下产物与 benchmark.json |
| `audit_out.py` | 独立核查完整单元的域、Z、尺寸、几何预算、容量及物性 | out → 默认 tmp/validation/reports/output-validation.json |
| `validate_region_accuracy.py` | 检查连续轮廓、拓扑、双预算与几何坐标独立性，可比较材料采样 | 中间 JSON → 默认 tmp/validation/reports/region-accuracy-default-3mm-validation.json |
| `refresh_viewer_outputs.py` | 仅替换预览内联 CSS/JS 并检查其余载荷一致性 | 预览 HTML → 原位更新及默认 tmp/validation/reports/viewer-output-validation.json |

`cases/` 是本地真实输入目录。合成夹具位于 `tests/fixtures/`，测试和基准过程输出位于 `tmp/`，正式结果位于 `out/`。
几何、标记或元数据改变时使用完整案例同步；CSS/JS 专项更新可用预览刷新工具。

运行、验证与打包命令统一见 [开发说明](../docs/development.md)，验证方法见 [验证说明](../docs/validation/README.md)，
规范来源与摘要见 [参考资料](../docs/reference/README.md)。
