# ADR 0001: 选 Python 作为技术栈

> 状态：Accepted
> 日期：2026-09-15
> 决策者：liubo

## 背景

全球资产配置组合需要数据获取 + 回测 + CLI + 飞书集成。备选技术栈：

- **Python** —— 金融数据生态最丰富
- **TypeScript/Node** —— Web 生态强，但金融库少
- **Go** —— 性能好但金融数据生态弱
- **Rust** —— 同上

## 决策

选 **Python 3.11+**。

## 理由

1. **数据源成熟**：`yfinance`、`akshare`、`tushare` 都是 Python first
2. **回测生态**：`pandas`、`numpy`、`vectorbt`、`backtrader`、`zipline`
3. **Pydantic v2** —— 数据校验一等公民，比 TS 的 zod 还干净
4. **Typer** —— CLI 体验好
5. **lark-oapi** —— 飞书官方 Python SDK
6. **liubo 已经在用 Python** —— 学习成本 = 0

## 后果

- **正面**：开发速度快，库多
- **负面**：性能不如 Go/Rust（但 MVP 数据量小，不是瓶颈）
- **缓解**：v0.2 可用 vectorbt 把回测改成向量化

## 备选方案

| 方案 | 否决理由 |
| --- | --- |
| TypeScript/Node | yfinance/akshare 没有 TS 绑定，要自写 HTTP client |
| Go | 没有靠谱的 yfinance/akshare 替代；CLI 体验差 |
| Rust | 学习曲线陡，MVP 不值得 |
