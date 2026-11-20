# pcbkit-rust / pcbeq-rust

`pcbkit-rust` 解析 ODB++ v8.1 单板，`pcbeq-rust` 计算 PCB 等效物性。两个产品独立安装，通过中间 JSON 交接。语言边界和数据流见[项目架构](docs/architecture.md)。

## 快速开始

源码构建需要 Rust 工具链与平台链接器。示例使用不随仓库检出的本地 `cases/minimal-test-odb`，也可换成自己的 ODB++ v8.1 单板目录或压缩包。

```powershell
uv sync
uv run pcbkit-rust export cases/minimal-test-odb -o out/example/rust/pcbkit --per-layer-png
cargo build -p pcbeq-rust --release
target/release/pcbeq.exe run out/example/rust/pcbkit/intermediate.json -o out/example/rust/pcbeq --grid-level standard
uv run pcbeq-rust plot out/example/rust/pcbeq/materialmap.json
```

解析器构建使用 `uv build --package pcbkit-rust`；等效器的单文件发布与可选 Python 管线见 [pcbeq 使用入口](packages/pcbeq-rust/README.md)。运行选项见各命令的 `--help`，案例管道见[维护工具](tools/README.md)。

测试范围、环境准备与定向运行命令见[测试指南](tests/README.md)。

## 项目文档

项目目录职责及需求、架构、接口、设计决策和领域资料的入口见[项目结构与文档索引](docs/README.md)。
