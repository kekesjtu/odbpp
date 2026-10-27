# pcbkit / pcbeq

ODB++ v8.1 单板解析器与 PCB 物性等效器，分别安装和分发，通过中间 JSON 协作。

| 软件 | 输入 | 输出 |
| --- | --- | --- |
| `pcbkit` | ODB++ 目录、tar / zip；曲线与坐标精度 | `intermediate.json`、SVG、交互式 HTML |
| `pcbeq` | 中间 JSON；材料、光栅与网格配置 | `materialmap.json`、材料掩膜、热图、region 与网格诊断 |

## 快速开始

在项目根目录使用 uv：

```powershell
uv sync --all-extras
uv run --all-extras pcbkit export cases/minimal-test-odb -o out/example/pcbkit --sagitta-mm 0.001 --digits 4
uv run --all-extras pcbeq run out/example/pcbkit/intermediate.json -o out/example/pcbeq --grid-level standard
```

常规网格提供 coarse / standard / fine，默认 standard。专家使用 `--max-cell-mm` 的一个值或 X/Y 两值设置最大边长。
光栅 `--px/--py` 成对指定，表示整板两个方向的采样像素数。
region 采用真实 Y 共同区间的竖条阶梯，默认完整轮廓退缩量 3 mm、面积损失预算 5%。
几何规划独立于材料采样分辨率；钻孔只在相交介质中按实心铜覆盖。
缺少材料配置时使用带来源的项目铜 / FR4 默认值并明确警告。

## 开发与案例管道

```powershell
uv run --all-extras pytest
uv run --all-extras python tools/sync_contract.py --check
uv run --all-extras python tools/run_pipeline.py cases/minimal-test-odb -o out/example
uv run --all-extras python tools/sync_out.py --force
uv run --all-extras python tools/audit_out.py
```

`out/<case>/pcbkit/` 保存解析结果，`pcbeq/` 保存等效结果，`pipeline/` 保存状态与日志。
本地真实输入位于 `cases/`，合成夹具位于 `tests/fixtures/`。
完整命令与独立打包见 [开发说明](docs/development.md)。

## 文件与文档

`packages/pcbkit/`、`packages/pcbeq/` 为两个产品，`contracts/` 为共享接口权威源，
`tests/` 为跨包测试，`tools/` 为维护工具，`docs/` 为项目文档，`tmp/` 为可再生工作文件。

- [需求与验收](requirements.md)
- [协作规则](AGENTS.md)
- [项目文档索引](docs/README.md)
- [技术栈与架构](docs/tech_stack.md)
- [中间 JSON](docs/interfaces/intermediate-json.md) 与 [MaterialMap](docs/interfaces/materialmap.md)
- [工具索引](tools/README.md)
