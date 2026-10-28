# 串并联 PCB 等效热导率

## 假设与几何

单元尺寸为 $L_x\times L_y\times L_z$，完整位于一个内接 region 和一个物理层内，Z 覆盖该层完整厚度。
两相材料沿整个层厚贯通，热导率只随 XY 变化：黑色为 $k_1$，白色为 $k_2$，均为有限正数。
输出采用正交各向异性张量 $\mathbf K=\operatorname{diag}(k_x,k_y,k_z)$。

串并联近似沿传热路径内部串联、不同路径之间并联。它保留两个轴向的图案排列信息，并省略非对角耦合项。

## 连续形式

固定 $y$ 的水平路径单位面积热阻为

$$
R_x(y)=\int_0^{L_x}\frac{dx}{k(x,y)},\qquad
k_x^{\mathrm{line}}(y)=\frac{L_x}{R_x(y)}.
$$

各水平路径并联，对 Y 平均；交换 X/Y 得到另一方向：

$$
k_x=\frac{1}{L_y}\int_0^{L_y}
\frac{L_x}{\displaystyle\int_0^{L_x}dx/k(x,y)}\,dy,
$$

$$
k_y=\frac{1}{L_x}\int_0^{L_x}
\frac{L_y}{\displaystyle\int_0^{L_y}dy/k(x,y)}\,dx.
$$

每个像素柱在 Z 方向为单一材料，不同柱并联，因此

$$
k_z=\frac{1}{L_xL_y}\int_0^{L_x}\int_0^{L_y}k(x,y)\,dy\,dx
=f k_1+(1-f)k_2,
$$

其中 $f$ 为黑色面积分数，也等于本模型中的材料 1 体积分数。

## 像素中心离散形式

单元采样包含 $N_x\times N_y$ 个等尺寸像素中心；允许 X 与 Y 的像素间距不同。
定义 $M_{ij}\in\{0,1\}$、$k_{ij}=M_{ij}k_1+(1-M_{ij})k_2$，其中 $i$ 为 X 列，$j$ 为 Y 行。

$$
f_{x,j}=\frac1{N_x}\sum_i M_{ij},\qquad
f_{y,i}=\frac1{N_y}\sum_j M_{ij},\qquad
f=\frac1{N_xN_y}\sum_{ij}M_{ij}.
$$

每行沿 X 调和平均后，各行取算术平均；Y 对称：

$$
\boxed{
\begin{aligned}
k_x&=\frac1{N_y}\sum_j\left(\frac{f_{x,j}}{k_1}+\frac{1-f_{x,j}}{k_2}\right)^{-1},\\
k_y&=\frac1{N_x}\sum_i\left(\frac{f_{y,i}}{k_1}+\frac{1-f_{y,i}}{k_2}\right)^{-1},\\
k_z&=f k_1+(1-f)k_2.
\end{aligned}}
$$

像素边界无需与单元边界对齐；中心按半开区间归属。该离散平均是采样近似，边界相位及细图案误差由分辨率收敛检查量化。
单元必须有足够有效采样且完整位于计算域内。

## 解析验证

令 $A(f)=f k_1+(1-f)k_2$，$H(f)=(f/k_1+(1-f)/k_2)^{-1}$。

| 图案 | kx | ky | kz |
| --- | --- | --- | --- |
| 全材料 1 | $k_1$ | $k_1$ | $k_1$ |
| 全材料 2 | $k_2$ | $k_2$ | $k_2$ |
| 水平条纹 | $A(f)$ | $H(f)$ | $A(f)$ |
| 竖直条纹 | $H(f)$ | $A(f)$ | $A(f)$ |

五项物性还需满足正值和材料上下界检查。密度与比热容按 [质量与能量守恒](密度和热容等效热模型推导.md) 求解。
钻孔在相交介质层按实心铜投影进入 $M$；铜层图案独立，见 [材料采样](../decisions/pcbeq/material-sampling.md)。
