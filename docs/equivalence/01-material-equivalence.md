> 当前正式输出已改为单个 region 层级 JSON，字段、行主序和未知 Z 规则见[MaterialMap 当前格式](09-region-materialmap.md)。下文的算法及历史测量继续有效；旧 CSV 字段/旁车描述已由该格式替代。

# MaterialMap 当前接口与物性模型

当前实现以 [内接架构说明](05-inscribed-architecture.md) 为准。
解析器输出一个物理 step 的[当前中间 JSON](07-intermediate-json.md)；等效器输出
region 层级 MaterialMap JSON。JSON 是两个模块之间唯一的数据接口。

## 完整实体单元

region 是内接二维矩形加所属物理层完整绝对 Z 区间的三维几何块。
物性网格完全位于单个 region 内，所有 region 边界传播为全局笛卡尔轴线，常规档位依据
板型与图案尺度给出物理尺寸；专家可控制最大边长，不再设置最小网格尺寸。region 采用真实 Y 区间竖条阶梯。逐层实体存在性在几何规划时确定；不会生成部分覆盖单元。
内部检查保持 domain_area_mm2=cell_area_mm2、domain_coverage=1、partial_domain=false；这些统计不再写入正式 MaterialMap。
密度和比热作用于完整单元体积，禁止按覆盖率缩减质量。

## 材料统计与公式

逐层光栅范围来自原始 step profile，默认密集光栅，Px/Py 可成对覆盖。
计算域、铜图案和相交钻孔独立扫描线处理：
铜层黑色 = step 域 AND layer 域 AND 铜图案。
介质层黑色 = step 域 AND layer 域 AND 相交钻孔。
介质初始铜图案为空。钻孔仅作用于介质，按 solid_copper 投影，不作为厚度层；铜层不受 DRILL 影响。
未知跨度或任何待等效层失败都阻止生成正式结果。

kx 为像素行内串联、行间并联；ky 为像素列内串联、列间并联。
kz 按铜面积分数加权，密度按体积分数加权，比热按质量加权。
详见两份 [推导](../derivation/串并联PCB等效热模型推导.md)、
[质量与热容推导](../derivation/密度和热容等效热模型推导.md)。

材料优先级为逐层、全局层类型、用户全局、项目默认。
默认铜 k/密度/cp 为 385/8960/385；FR4 为 0.3/1850/1100，单位分别为
W/(m·K)、kg/m³、J/(kg·K)。使用默认值警告且记录来源；
可用 layer_types.copper/dielectric 或 layers.<name> 配置两相参数。

## 常规命令

    uv sync --all-extras
    uv run --all-extras odbpp equivalent --from-json out/<case>/<step>.json --grid-level standard -o out/<case>

geometry 不读取光栅配置。采样不足提示提高分辨率，不改变坐标。
旧 --rectify-tolerance 和覆盖率质量语义已退役。JSON 网格线还原的实际毫米边界是
全局单元几何；x_lines/y_lines 为共同的完整轴线集合。
中间 JSON / 运行诊断保留 best-effort Z、可信标记和来源；正式 MaterialMap 的未知 Z 使用 null。

完整算法、默认预算、诊断与限制见 [05](05-inscribed-architecture.md)。
真实案例重导出、前后性能与收敛证据见
[修复 09](../fixes/09-inscribed-architecture.md) 和
[验证数据](architecture-validation.json)。
