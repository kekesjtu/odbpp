# pcbkit-rust

Rust features/profile 解析与整层几何合成、Python 输入适配和预览组成的 ODB++ 解析器。
命令：`pcbkit-rust export <ODB++> -o <目录>`。

默认交付中间 JSON 和 SVG、HTML 预览。`--per-layer-png` 和 `--per-layer-svg` 可在 `layers/` 下另存逐层图片。PNG 文件名包含 matrix 行号和图层名，SVG 文件名使用图层名。逐层 PNG 共用视窗和毫米比例。`--only-json` 省略全部预览，保留中间 JSON、运行诊断和产物清单，适用于数值管线与性能测量。

独立构建：`uv build --package pcbkit-rust`。源码构建需要 Rust 和平台 C/C++ 链接器。
Python 输入、契约校验和预览代码由本包独立携带；记录、符号缓存和中间几何由本包 Rust 扩展持有，Python 通过原生句柄调用整层合成。PNG 使用 Pillow。Python API 使用 `pcbkit_rust` 命名空间。
