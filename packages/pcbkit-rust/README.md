# pcbkit-rust

Rust features/profile 解析与整层几何合成、Python 输入适配和预览组成的 ODB++ 解析器。
命令：`pcbkit-rust export <ODB++> -o <目录>`。

添加 `--per-layer-png` 在 `layers/` 下输出每一层的 PNG，文件名包含 matrix 行号和图层名；各层共用视窗和毫米比例。`--only-json` 仅输出中间 JSON，适用于数值管线与性能测量。`--per-layer-svg` 可另存逐层 SVG。

独立构建：`uv build --package pcbkit-rust`。源码构建需要 Rust 和平台 C/C++ 链接器。
Python 输入、契约校验和预览代码由本包独立携带；记录、符号缓存和中间几何由本包 Rust 扩展持有，Python 通过原生句柄调用整层合成。PNG 使用 Pillow。Python API 使用 `pcbkit_rust` 命名空间。
