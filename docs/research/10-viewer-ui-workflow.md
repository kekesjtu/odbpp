# 本机 ODB++ Viewer UI 观察（2026-10-03）

用户指定后台进程 PID 22404。通过 Windows computer-use 的控件树与截图，
确认进程对应已安装 Viewer 2604 的 `odbviewer.exe`。初始窗口为 Product
Model List，尚未打开板图。导入本项目 `cases/kicad-video-odb.tgz` 后，
实际观察主窗口和只读 Matrix。前两次截图抓取失败，随后主窗口截图成功。

## 直接观察

- 主窗口左侧为 ODB++ Layer List，图层名单与颜色按叠层组织；左下为板框
  导航，右侧为板图。初始只显示板框，可逐层查看。
- 工具栏包含 Matrix、Zoom In/Out/Area/Home、Previous Zoom、Pan XY、
  Highlight、View Properties、Toggle Units、Measure between Features。
- Matrix 的列包括 Layers、Type、Subtype、Context、Polarity 和 Reference；
  COMPONENT、DOCUMENT 和板内材料层明确区分。该案例的介质 subtype 有
  core/prepreg，DOCUMENT 常为 misc context。
- 主窗口状态栏显示 step、层数、尺寸、来源和板面积。该 Viewer 显示的尺寸
  为 12.285×4.2，不能直接当作 mm 与本解析器比较；输出统一毫米。

安装目录官方帮助：
`E:/SiemensEDA/Valor/ODB++_Viewer/odbviewer_2604/edir/all/manuals/docs/odbv_user/elh1729081203280/00002.html`。
其中说明图元 histogram、鼠标 XY 坐标、导航、矩阵和测量用途。本文将
直接观察和帮助内容分开：未逐一实测 Viewer 的所有工具，也没有检查其内部
解析代码。图元统计参考帮助描述；本项目按已解析记录实现，不复制厂商实现。

## 本项目采用的交互

1. 复选框控制显示；行选择查看属性，双击或 Enter 隔离。列表搜索不改变可见性。
2. 独立显示原始板框与选中层域，实体铜图案不能代替层域。
3. 层叠矩阵按原始行号排列，展示绝对 Z、厚度可信状态与来源，及钻孔跨度。
4. 维持单文件离线 HTML，加入板框导航、选中层定位和两点毫米测距。
   当前测量是任意两点距离，不是 Viewer 的图元吸附测量。
5. 输入图元统计保留类型/极性/符号/有效作用域。符号 MM 作用域的参数单位
   为 µm，INCH 作用域为 mil；材料多边形仍是解析器完全正向规整后的结果。

导入期间 Viewer 自动在压缩包旁生成了解包目录。该派生目录及对应重复输出
移入 `build/viewer-reference-import`、`build/viewer-reference-output`，避免与
原始 tgz 案例混淆；原压缩包保留。
