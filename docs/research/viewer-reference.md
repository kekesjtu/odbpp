# Siemens ODB++ Viewer 参考范围

## 本机观察对象

项目的参考记录对应 Viewer 2604.1，安装入口为 `E:/SiemensEDA/Valor/ODB++_Viewer/odbviewer_2604/edir/get/odbviewer.exe`。
安装树包含 Viewer 用户手册与 HTML 帮助，官方来源为 [ODB++ Viewer](https://odbplusplus.com/design/download/odb-viewer/)。
该版本号和路径标识本地观察对象，不表示对外部软件最新版本的判断。

主窗口提供图层列表、板框导航、板图、缩放 / 平移 / 测量和 Matrix。
matrix 展示层名、类型、子类型、上下文、极性与引用；状态栏尺寸需要结合显示单位核对。
本项目借鉴图层检查和矩阵核验流程，交互约定见 [查看器决策](../decisions/viewer-interaction.md)。

## 黑盒核验

一次只改变一个字段，导入已知小作业，分别比较接受状态、单位、符号尺寸、极性、层级 profile、孔洞和钻孔跨度。
Viewer 观察与官方格式语义分别记录，显示容错不直接定义 Parser 的规范要求。
截图和人工显示核验辅助数值与几何断言，不能替代材料公式、完整域和收敛验证。

## 接口证据边界

参考安装树包含原生程序模块和 `Valor.py` NPI 脚本材料，Viewer 手册描述导入与检查用途。
这些材料本身不足以证明 Viewer 提供多边形导出 API 或可复用解析 SDK。
本项目的解析实现以官方格式、独立源设计和公开结构调研为依据。
