# pcbkit-rust

Rust 数值核心与 Python 输入、契约校验及 SVG/HTML 可视化组成的 ODB++ 解析器。
命令：`pcbkit-rust export <ODB++> -o <目录>`。参数与 `pcbkit` 一致。

独立构建：`uv build --package pcbkit-rust`。源码构建需要 Rust 和平台 C/C++ 链接器。
Python 输入、契约校验和预览代码由本包独立携带；几何离散、布尔及扫掠直接调用本包 Rust 扩展，不依赖 `pcbkit` 包或其源码。Python API 使用 `pcbkit_rust` 命名空间。
