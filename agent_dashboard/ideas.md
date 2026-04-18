# 极简业务看板 设计方案

## 设计方向

<response>
<probability>0.07</probability>
<text>
**工业仪表盘风格 (Industrial Control Room)**

- **Design Movement**: 工业控制室 + 数据密度美学
- **Core Principles**: 信息密度优先；高对比度配色；功能即形式；无装饰主义
- **Color Philosophy**: 深炭灰底色（#0F1117），电光蓝高亮（#00D4FF），警告橙（#FF6B35），成功绿（#00E676）。色彩仅用于传达状态，不做装饰。
- **Layout Paradigm**: 左侧固定导航栏 + 右侧主内容区，顶部状态条显示全局 Consul 健康度。内容区采用不对称的黄金比例分割。
- **Signature Elements**: 等宽字体（JetBrains Mono）用于所有状态值；细线边框 + 内发光效果；状态指示灯（脉冲动画）
- **Interaction Philosophy**: 所有操作均有即时反馈；危险操作需二次确认弹窗；悬停时显示完整元数据
- **Animation**: 状态变更时数字滚动动画；节点连线流动动画；FAILED 状态红色闪烁
- **Typography System**: JetBrains Mono（状态值/ID）+ Inter（说明文字），严格的字号层级（12/14/16/24px）
</text>
</response>

<response>
<probability>0.06</probability>
<text>
**极简纸质感 (Minimal Paper)**

- **Design Movement**: 瑞士国际主义 + 纸质触感
- **Core Principles**: 网格严格对齐；排版驱动设计；克制的色彩使用；内容至上
- **Color Philosophy**: 米白底色（#FAF9F7），炭黑文字（#1A1A1A），单一强调色——靛蓝（#3B4EFF）。状态用填充密度而非颜色区分。
- **Layout Paradigm**: 12 列网格系统，顶部全宽导航，内容区按信息权重分配列宽。任务 DAG 图占据页面 2/3 宽度。
- **Signature Elements**: 细线分隔符；无圆角卡片；衬线字体标题
- **Interaction Philosophy**: 点击展开详情，而非弹窗；所有操作内联完成
- **Animation**: 仅有 opacity 和 height 过渡，无位移动画
- **Typography System**: Playfair Display（标题）+ Source Sans Pro（正文），严格的模块化字号比例
</text>
</response>

<response>
<probability>0.08</probability>
<text>
**深色科技感 (Dark Tech Dashboard) — 选定方案**

- **Design Movement**: 现代 DevOps 工具美学（参考 Datadog、Linear、Vercel Dashboard）
- **Core Principles**: 深色背景降低视觉疲劳；状态颜色语义化；层级通过背景亮度区分；动效服务于信息传达
- **Color Philosophy**: 近黑底色（#0D1117），略亮卡片面（#161B22），边框用低饱和灰（#30363D）。强调色：蓝（#58A6FF 进行中）、绿（#3FB950 完成）、橙（#D29922 等待）、红（#F85149 失败）、紫（#BC8CFF 测试中）。
- **Layout Paradigm**: 左侧 240px 固定侧边栏（需求列表）+ 右侧主区域（顶部状态摘要卡片行 + 中部 DAG 拓扑图 + 底部任务详情表格）
- **Signature Elements**: 圆角徽章（Badge）显示状态；连线 DAG 图用 SVG 绘制；任务卡片悬停时边框高亮
- **Interaction Philosophy**: 侧边栏点击需求切换主视图；主视图顶部操作按钮（PAUSE/RESUME/ABORT）带确认对话框；任务节点点击展开产物链接
- **Animation**: 状态变更时 Badge 颜色平滑过渡（300ms ease）；DAG 节点加载时从上到下依次 fade-in；FAILED 节点轻微 shake 动画
- **Typography System**: Space Grotesk（标题/ID，几何感强）+ Inter（正文），代码/Hash 值用 JetBrains Mono
</text>
</response>

## 选定方案

选择**深色科技感 (Dark Tech Dashboard)**，与 DevOps 工具链的视觉语言保持一致，降低研发团队的认知切换成本，同时通过语义化颜色让任务状态一目了然。
