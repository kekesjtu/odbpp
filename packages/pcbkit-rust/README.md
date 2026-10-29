# pcbkit-rust

Rust 数值核心与 Python 输入、契约校验及 SVG/HTML 可视化组成的 ODB++ 解析器。
命令：`pcbkit-rust export <ODB++> -o <目录>`。参数与 `pcbkit` 一致。

独立构建：`uv build --package pcbkit-rust`。源码构建需要 Rust 和平台 C/C++ 链接器。
Python API 可在 `with pcbkit_rust.backend():` 内使用 pcbkit；上下文结束后恢复调用方后端。
