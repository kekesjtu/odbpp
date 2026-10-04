# HTML 查看器显示与交互性能优化

日期：2026-10-04。基线：`dbc45e7`；实现：`236b7e7`。本轮仅修改 `svg.py` 内联的查看器 CSS/JS；
解析、SVG 多边形、JSON、网格、材料采样和物性公式保持不变。

## 改动

- 平移、缩放和毫米状态栏采用 `requestAnimationFrame`，同一帧集中写入显示状态。
  每个输入事件的位移和缩放仍按顺序计算，没有丢弃事件或滚轮增量。
- 坐标转换读取浏览器的 SVG viewBox CTM，组合待显示的逻辑变换与原有 Y 翻转。
  保留 SVG transform 的 float32 表示和原有缩放锚点修正，避免改变坐标读数。
  定位图层在读取实际边界前刷新待显示变换，保留隐藏层恢复显示和居中行为。
- 铜图案、板框、计算域和测量线不再参与复杂路径命中检测；交互仍由主 SVG 接收。
  原始路径、精度、填充规则、颜色、透明度和线框显示均保留。
- 缓存图层索引、复选框、搜索文本、计算域与矩阵箭头节点；跳过重复样式写入，
  复用当前层属性内容。再次选择或切换属性时仍重置属性滚动位置。
- 矩阵先读取所需端点的行中心，再统一写入箭头；共享端点只读取一次，
  尺寸未变时跳过重复更新。图层排序使用 DocumentFragment 批量移动现有节点。

HTML 仍自包含，可离线直接打开，没有新增运行时库。更新在下一显示帧生效；
这是浏览器绘制调度优化，逻辑状态在输入事件中立即更新。

## 性能测量

使用本机 Chrome headless，桌面视口 1280 × 800。每个场景新建页面，向同一真实
PCB 查看器连续派发 400 个事件，重复 5 次取中位数。下表单位 ms，为事件派发及
同步处理耗时，**不包含后续绘制和显示器刷新，不应当作帧率或整页加载加速比**。
原始 CDP 布局/样式指标与环境版本见
[`viewer-performance-validation.json`](viewer-performance-validation.json)。

| 案例 | 鼠标移动：前 → 后 | 拖动：前 → 后 | 滚轮：前 → 后 |
|---|---:|---:|---:|
| minimal-test-odb | 9.90 → 2.00 | 12.80 → 2.60 | 8.10 → 2.40 |
| kicad-kitchen-sink | 9.60 → 2.10 | 12.70 → 2.50 | 8.70 → 2.50 |
| kicad-video-odb.tgz | 10.00 → 1.90 | 12.90 → 2.50 | 8.60 → 2.50 |
| designodb-rigidflex.tgz | 9.70 → 2.30 | 13.10 → 2.60 | 9.70 → 2.50 |

此次事件处理耗时降低 70.4%～81.0%。同一帧的 400 次拖动中，root transform 写入
从 400 次降到 1 次；滚轮从 800 次降到 1 次。鼠标坐标文本写入从 400 次降到 1 次。
对应 CDP LayoutCount：鼠标移动 400 → 1，拖动 799 → 1，滚轮 800 → 1。
独立事件跨越多个显示帧时会逐帧绘制，不能将这些同帧数字外推为所有硬件的收益。
复杂 SVG 的实际绘制和初次解析成本仍存在。

## 功能验证

四个真实案例合计 159 个浏览器检查点覆盖：

- 桌面鼠标悬停、连续拖动/滚轮、移到 SVG 外继续拖动、浏览器真实指针事件；
- 放大/缩小、全板适配、定位选中层、缩略导航、板框/计算域/线框切换；
- 复选框、全选/全隐藏/铜层/反选/隔离、双击隔离、搜索与两种排序；
- 属性开关与滚动复位、测距与 Escape 清除、键盘快捷键；
- 矩阵钻孔箭头选择、窗口调整、矩阵滚动、窄屏布局；
- 缩放限制 0.02/5000 及焦点固定；极限缩放的浮点误差与旧版相同。

坐标与测距显示文本、属性、图层可见性、复选框、矩阵状态和箭头路径逐项相同。
最终同状态截图逐像素一致；变换矩阵最大绝对差约 `1.15e-5`（平移采用 viewBox 的 mm），
来自浏览器浮点运算顺序，比较容差为 0.002；显示文本仍按原有三位小数精确对照。
浏览器页面没有 JavaScript 错误。全套 Python 测试 737 项通过，最终查看器专项测试
再次通过 73 项。

`out/` 的 18 个交互查看器已更新，仅替换内联 CSS/JS。页面其余内容（含 SVG
路径、元数据和检查面板）逐字符不变，见
[`viewer-output-validation.json`](viewer-output-validation.json)。掩膜、热图、CSV 和 JSON
无需重新计算。本轮没有增加材料采样或几何策略参数。

## 复现与更新

```powershell
uv sync --all-extras
uv run --all-extras python -m pytest
uv run --all-extras --with playwright python tools/validate_viewer_performance.py --baseline-ref dbc45e7 --repeat 5
uv run --all-extras python tools/refresh_viewer_outputs.py
```

浏览器验证工具使用本机 `C:/Program Files/Google/Chrome/Application/chrome.exe`；
Playwright 仅由 uv 为验证提供，不加入项目运行时依赖。验证读取 Git 基线的 CSS/JS，
与当前版本使用相同的真实板路径和元数据，未切换工作区。
`refresh_viewer_outputs.py` 仅适用于 CSS/JS 更新；修改几何、HTML 标记或元数据时
继续使用 `tools/sync_out.py --force`。

浏览器对照页面保存在 Git 忽略的 `build/viewer-validation/`。本轮已核对删除目标均在
该目录内，但执行策略拒绝逐文件删除，返回 `blocked by policy`，未提供进一步原因。
这些对照页因此保留，正式报告与输出独立保存。
