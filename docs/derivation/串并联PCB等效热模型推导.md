# 串并联 PCB 等效热模型的公式推导

## 1. 假设

1. **PCB 采用分层建模。**  
   每一个物理层都有自己的二维材料图案，并具有确定的层厚。

2. **物性单元在厚度方向完整对应一个物理层。**
   单元的绝对 Z 起止等于所属层的 Z 区间，层内不再沿 Z 分割；单元完整位于一个内接 region 内，不跨物理层。region 是三维几何建模块，单元是其中赋予物性的计算块。

3. **每个物理层内部的材料图案只随 $x,y$ 变化。**  
   该层内仅有两种材料（光栅化后，一个为白色，一个为黑色）：

   $$
   k(x,y)=
   \begin{cases}
   k_1,&\text{黑色区域},\\
   k_2,&\text{白色区域}.
   \end{cases}
   $$

   且同一物理层中的材料沿整个层厚方向完全贯通。
   即对于某个 $(x,y)$ 位置，如果该位置在二维图案中是黑色，那么对应材料 $k_1$ 从该层顶面一直贯通到底面；若为白色，则材料 $k_2$ 同样贯通整个层厚。因此：

   $$
   \frac{\partial k}{\partial z}=0
   $$

   在单个物理层内部成立。

4. **每个代表块最终只保留三个主方向等效热导率。**  
   即认为 block 是正交各向异性的：

   $$
   \mathbf K_b=
   \begin{bmatrix}
   k_{x,b}&0&0\\
   0&k_{y,b}&0\\
   0&0&k_{z,b}
   \end{bmatrix}.
   $$

   不考虑 $k_{xy},k_{xz},k_{yz}$ 等非对角耦合项。

5. **采用简单串并联近似。**  
   对每个方向：
   - 沿传热方向排列的材料视为**串联**；
   - 与传热方向垂直的不同导热路径视为**并联**。

   因此：
     - $x$ 方向：每一条水平像素路径内部串联，不同行之间并联；
     - $y$ 方向：每一条竖直像素路径内部串联，不同列之间并联；
     - $z$ 方向：由于每个像素材料都贯穿整个层厚，因此不同像素柱之间完全并联。

## 2. 推导

设一个代表块尺寸为

$$
L_x\times L_y\times L_z
$$

并且该 block 完整位于 PCB 的某一物理层内。该物理层中的二维图案沿整个厚度 $L_z$ 贯通，因此

$$
k=k(x,y)=
\begin{cases}
k_1,&\text{黑色图案材料},\\[2mm]
k_2,&\text{白色基底材料}.
\end{cases}
$$

最终每个 block 赋予正交各向异性热导率张量

$$
\boxed{
\mathbf K_b=
\begin{bmatrix}
k_{x,b}&0&0\\
0&k_{y,b}&0\\
0&0&k_{z,b}
\end{bmatrix}
}
$$

### 2.1 连续积分形式：串并联等效

#### 2.1.1 $x$ 方向

考虑固定的 $y$。

从 $x=0$ 到 $x=L_x$ 的不同材料沿热流方向依次排列，因此是**串联**。

这一条水平路径的单位面积热阻为

$$
R_x(y)
=
\int_0^{L_x}\frac{dx}{k(x,y)}.
$$

定义该水平路径的等效热导率 $k_x^{\rm line}(y)$，则

$$
\frac{L_x}{k_x^{\rm line}(y)}
=
\int_0^{L_x}\frac{dx}{k(x,y)},
$$

所以

$$
k_x^{\rm line}(y)
=
\frac{L_x}
{\displaystyle\int_0^{L_x}\frac{dx}{k(x,y)}}
$$

而不同 $y$ 位置的水平路径之间互相**并联**，因此：

$$
\boxed{
k_{x,b}
=
\frac{L_x}{L_y}
\int_0^{L_y}
\left[
\int_0^{L_x}\frac{dx}{k(x,y)}
\right]^{-1}
dy
}
$$

#### 2.1.2 $y$ 方向

完全类似。

固定一个 $x$，沿 $y$ 方向的材料是串联的，因此

$$
k_y^{\rm line}(x)
=
\frac{L_y}
{\displaystyle
\int_0^{L_y}\frac{dy}{k(x,y)}
}.
$$

不同 $x$ 位置的竖直路径彼此并联，因此

$$
\boxed{
k_{y,b}
=
\frac{L_y}{L_x}
\int_0^{L_x}
\left[
\int_0^{L_y}\frac{dy}{k(x,y)}
\right]^{-1}
dx
}
$$

#### 2.1.3 $z$ 方向

你的关键假设是：

> 一层 PCB 中，每种材料都沿整个层厚度贯通。

因此对于任意 $(x,y)$，沿 $z$ 方向都只有一种材料，不存在 $k_1/k_2$ 的厚度方向串联。

不同 $(x,y)$ 位置对应大量沿 $z$ 方向的导热柱，它们彼此**并联**。

因此

$$
\boxed{
k_{z,b}
=
\frac{1}{L_xL_y}
\int_0^{L_x}
\int_0^{L_y}
k(x,y)\,dy\,dx
}
$$

如果定义 block 中黑色材料的面积分数为 $\phi_b=\frac{A_1}{L_xL_y},$ 那么直接得到

$$
\boxed{
k_{z,b}
=
\phi_b k_1+(1-\phi_b)k_2
}
$$

#### 2.1.4 三个连续公式汇总

因此，对于任意二维图案 $k(x,y)$：

$$
\boxed{
k_{x,b}
=
\frac{1}{L_y}
\int_0^{L_y}
\frac{L_x}
{\displaystyle\int_0^{L_x}\frac{dx}{k(x,y)}}
\,dy
}
$$

$$
\boxed{
k_{y,b}
=
\frac{1}{L_x}
\int_0^{L_x}
\frac{L_y}
{\displaystyle\int_0^{L_y}\frac{dy}{k(x,y)}}
\,dx
}
$$

$$
\boxed{
k_{z,b}
=
\frac{1}{L_xL_y}
\int_0^{L_x}
\int_0^{L_y}
k(x,y)\,dy\,dx
}
$$

这三个式子就是当前模型下的**通用串并联积分公式**。

---

### 2.2 像素离散形式

现在假设一个 block 内包含

$$
N_x\times N_y
$$

个等尺寸像素。

定义

$$
M_{ij}=
\begin{cases}
1,&\text{黑色},\\
0,&\text{白色},
\end{cases}
$$

以及

$$
\boxed{
k_{ij}
=
M_{ij}k_1+(1-M_{ij})k_2
}
$$

即

$$
k_{ij}=
\begin{cases}
k_1,&M_{ij}=1,\\
k_2,&M_{ij}=0.
\end{cases}
$$

#### 2.2.1 $x$ 方向

固定第 $j$ 行。

这一行中的 $N_x$ 个像素沿 $x$ 方向串联，因此：

$$
k_{x,j}
=
\left[
\frac{1}{N_x}
\sum_{i=1}^{N_x}
\frac{1}{k_{ij}}
\right]^{-1}
$$

而 $N_y$ 行之间并联，因此

$$
\boxed{
k_{x,b}
=
\frac{1}{N_y}
\sum_{j=1}^{N_y}
\left[
\frac{1}{N_x}
\sum_{i=1}^{N_x}
\frac{1}{k_{ij}}
\right]^{-1}
}
$$

对于 $x$ 方向，定义第 $j$ 行黑色像素比例

$$
\phi_{x,j}
=
\frac{1}{N_x}
\sum_{i=1}^{N_x}M_{ij}
$$

因为这一行中黑白材料是串联关系，所以

$$
\frac{1}{k_{x,j}}
=
\frac{\phi_{x,j}}{k_1}
+
\frac{1-\phi_{x,j}}{k_2}.
$$

于是

$$
\boxed{
k_{x,b}
=
\frac{1}{N_y}
\sum_{j=1}^{N_y}
\left(
\frac{\phi_{x,j}}{k_1}
+
\frac{1-\phi_{x,j}}{k_2}
\right)^{-1}
}
$$

#### 6. $y$ 方向

固定第 i 列。

同上:

$$
\boxed{
k_{y,b}
=
\frac{1}{N_x}
\sum_{i=1}^{N_x}
\left[
\frac{1}{N_y}
\sum_{j=1}^{N_y}
\frac{1}{k_{ij}}
\right]^{-1}
}
$$

同理，定义第 $i$ 列黑色像素比例

$$
\phi_{y,i}
=
\frac{1}{N_y}
\sum_{j=1}^{N_y}M_{ij},
$$

则

$$
\boxed{
k_{y,b}
=
\frac{1}{N_x}
\sum_{i=1}^{N_x}
\left(
\frac{\phi_{y,i}}{k_1}
+
\frac{1-\phi_{y,i}}{k_2}
\right)^{-1}
}
$$

#### 2.2.3 $z$ 方向

每个像素对应一个贯穿整个 block 厚度的导热柱。

所有像素柱沿 $z$ 方向并联，所以

$$
\boxed{
k_{z,b}
=
\frac{1}{N_xN_y}
\sum_{i=1}^{N_x}
\sum_{j=1}^{N_y}
k_{ij}
}
$$

定义整个 block 的黑色像素比例：$\phi_b=\frac{1}{N_xN_y}\sum_{i=1}^{N_x}\sum_{j=1}^{N_y}M_{ij}$，则

$$
\boxed{
k_{z,b}
=
\phi_bk_1+(1-\phi_b)k_2
}
$$

#### 2.2.4 最终推荐使用的离散公式

对于实际的光栅化 PCB 数据，直接使用下面这一组：

$$
\boxed{
\begin{aligned}
k_{x,b}
&=
\frac{1}{N_y}
\sum_{j=1}^{N_y}
\left(
\frac{\phi_{x,j}}{k_1}
+
\frac{1-\phi_{x,j}}{k_2}
\right)^{-1},
\\[3mm]
k_{y,b}
&=
\frac{1}{N_x}
\sum_{i=1}^{N_x}
\left(
\frac{\phi_{y,i}}{k_1}
+
\frac{1-\phi_{y,i}}{k_2}
\right)^{-1},
\\[3mm]
k_{z,b}
&=
\phi_b k_1+(1-\phi_b)k_2.
\end{aligned}
}
$$

其中

$$
\phi_{x,j}=\frac{1}{N_x}\sum_iM_{ij},
\qquad
\phi_{y,i}=\frac{1}{N_y}\sum_jM_{ij},
\qquad
\phi_b=\frac{1}{N_xN_y}\sum_{ij}M_{ij}.
$$

最终：

$$
\boxed{
\mathbf K_b
=
\operatorname{diag}
\left(k_{x,b},k_{y,b},k_{z,b}\right)
}
$$
