# pcbeq-rust

Rust 数值核心与 Python 输入、几何审计、报告及 Matplotlib 图表组成的 PCB 等效器。
命令：`pcbeq-rust run <intermediate.json> -o <目录>`。参数与 `pcbeq` 一致。

独立构建：`uv build --package pcbeq-rust`。源码构建需要 Rust 和平台 C/C++ 链接器。
Python API 可在 `with pcbeq_rust.backend():` 内使用 pcbeq；两个产品仍只通过中间 JSON 交接。
