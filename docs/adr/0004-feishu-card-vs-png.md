# ADR 0004: 飞书可视化用原生 Chart Card（不用 PNG）

> 状态：Accepted
> 日期：2026-09-15
> 决策者：liubo

## 背景

回测结果要发飞书给 liubo 看。两种实现：

1. **PNG 图片**（matplotlib 生成）—— 通过 botmux 发
2. **飞书原生 chart card**（interactive card with chart_spec）—— 通过 lark-oapi 直发

## 决策

**用飞书原生 chart card**，调 lark-oapi SDK 直接发。

## 理由

1. **交互性**：飞书图表支持 hover 看数值、切换 series；PNG 是死的
2. **体积小**：卡片 JSON ~10KB，PNG ~500KB
3. **样式统一**：飞书主题色，不会被 matplotlib 主题丑到
4. **liubo 明确要求"采用飞书卡片组件"**

## 后果

- **正面**：用户体验好、传输小、可交互
- **负面**：
  - 失去本地存档（PNG 可以存档，card 数据在飞书消息里）
  - 调试难（看不到渲染结果，必须真发飞书）
- **缓解**：
  - `gap publish --dry-run` 输出 card JSON 到 stdout
  - 数据同时存 `data/output/last_publish.json`（备查）
  - `tests/integration/` 跑一个 sandbox chat 真发验证

## 备选方案

| 方案 | 否决理由 |
| --- | --- |
| PNG + matplotlib | 没交互性；liubo 否决 |
| Plotly HTML 文件 | 飞书不发执行 JS，打开是空的 |
| 飞书多维表格 | 只能存数据不能画图 |
| 飞书文档 + 图表 | 操作复杂，liubo 不需要编辑能力 |

## 后续

v0.2 可以同时支持 `--format png` 给有特殊需求的人。
