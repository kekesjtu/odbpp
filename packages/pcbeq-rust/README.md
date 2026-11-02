# pcbeq-rust

中间 JSON 读取与校验、region 规划、几何审计、网格、材料采样、等效、MaterialMap 和运行诊断全部在 Rust 内执行。
Python 仅作命令启动及 MaterialMap 图表渲染；不读取输入板 JSON、不参与数值计算或 MaterialMap 写入。
命令：`pcbeq-rust run <intermediate.json> -o <目录>`；`--no-heatmap --no-regions --no-grids --no-gallery` 仅生成数值结果。
图表只消费 MaterialMap，不导出采样光栅掩膜。
每个 region 独立光栅化，默认仅在局部加密；`--px/--py` 以原始整板范围定义显式参考采样密度与像素中心相位。诊断记录各 region 的实际光栅尺寸、物理间距和总/峰值像素数。

独立构建：`uv build --package pcbeq-rust`。源码构建需要 Rust 和平台 C/C++ 链接器。
文件 API：`pcbeq_rust.native.run(input_path, output_directory, **options)`；PyO3 在完整原生流程期间释放 GIL。
几何审计使用 GEOS C API，默认从 Shapely wheel 的原生库目录定位 GEOS，也可通过 `PCBEQ_GEOS_LIBRARY` 指定 C 库绝对路径。整个计算流程不调用 Shapely Python API。
两个产品仍只通过中间 JSON 交接。
