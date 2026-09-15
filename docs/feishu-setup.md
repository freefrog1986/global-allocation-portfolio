# 飞书机器人配置

> 一步一步教你建飞书 app、配权限、拿到凭证，让 `gap publish` 能真把卡片发到话题里。

## 流程概览

1. 在[飞书开放平台](https://open.feishu.cn/)创建企业自建应用
2. 开启「机器人」能力 → 拿到 App ID 和 App Secret
3. 把机器人拉进目标 chat（话题群 / 单聊）
4. 拿到 chat_id（话题群的 chat_id 是 `oc_xxx`，话题 ID 是 `om_xxx`）
5. 配置权限（im:message 等）
6. 把凭证灌进 `gap`
7. `gap publish --dry-run` 测试

---

## 1. 创建飞书应用

打开 https://open.feishu.cn/app ，点击「创建企业自建应用」。

- 名字：`gap-reporter`（或随便起）
- 描述：全球资产配置组合回测报告机器人
- 头像：可选

创建完你会跳到应用详情页。

## 2. 开启机器人能力

应用详情 → 「应用能力」 → 「机器人」 → 「启用」。

不需要配回调 URL；我们只用发消息能力。

## 3. 拿到 App ID 和 App Secret

应用详情 → 「凭证与基础信息」：

- **App ID**：长这样 `cli_xxxxxxxxxxxx`
- **App Secret**：点击「查看」拿到一串字符

这两个就是 `gap` 用来调飞书 API 的身份。

> ⚠️ App Secret 等同于密码，绝不能 commit 到 git。

## 4. 配置权限

应用详情 → 「权限管理」 → 搜索并开通：

- `im:message` — 给机器人发消息的权限（含 `im:message:send_as_bot`）
- `im:message.group_at_msg` — 在群里 @ 机器人的消息读取（如果想 @）
- `im:resource` — 上传图片（可选；当前 `gap publish` 不发图，但以后可能用）

保存后需要去「版本管理与发布」发一版。

## 5. 发布版本 + 让管理员审批

应用详情 → 「版本管理与发布」：

1. 填版本号（随便填，`1.0.0`）和说明
2. 点「保存并发布」
3. 因为是企业自建应用，你的企业管理员会收到审批请求；管理员通过后机器人就「活」了

## 6. 把机器人拉进 chat

在你想收到回测报告的飞书群里：

1. 群设置 → 「群机器人」 → 「添加机器人」 → 搜索你的 app 名字 → 添加

或者在话题组里回复一下 @机器人 触发激活。

## 7. 拿到 chat_id

最简单的方式：

1. 进群 → 看浏览器地址栏 URL，类似 `https://feishu.cn/messenger/oc_abc123def456...`
2. `oc_xxx` 那串就是 chat_id
3. 如果是**话题群**（thread），会有一个 `om_xxx` 的话题 ID；这种情况下 `gap publish` 默认发到 chat 顶层，要在话题里发需要额外参数（目前未实现，未来加）

可以在飞书里发条消息测试，看下消息的「查看 API Explorer」里的真实 chat_id。

## 8. 把凭证告诉 `gap`

两种方式，**二选一**：

### A. 环境变量（适合 CI / 容器 / 临时）

```bash
export FEISHU_APP_ID="cli_xxxxxxxxxxxx"
export FEISHU_APP_SECRET="your_app_secret_here"
export FEISHU_CHAT_ID="oc_abc123def456"
```

### B. 配置文件（适合本地）

写到 `~/.config/gap/credentials.json`：

```json
{
  "app_id": "cli_xxxxxxxxxxxx",
  "app_secret": "your_app_secret_here",
  "chat_id": "oc_abc123def456"
}
```

注意 `XDG_CONFIG_HOME` —— 如果你设了，会用 `$XDG_CONFIG_HOME/gap/credentials.json` 而不是 `~/.config/gap/credentials.json`。

**优先级**：环境变量 > 配置文件。如果两者都有同一字段，环境变量赢。

### 别忘了 `chmod 600`

```bash
chmod 600 ~/.config/gap/credentials.json
```

凭证里有 secret，文件权限锁紧一点。

## 9. 测试

```bash
# 不真发，只生成卡片 JSON
gap publish 60_40 --period 5y --dry-run

# 真发
gap publish 60_40 --period 5y
```

成功的话飞书群里会出现一张蓝色 header 的卡片，包含：

- 标题 + 周期 + 初始/终值/总收益
- 资金曲线（折线图）
- 当前权重（饼图）
- 关键指标表格（CAGR、夏普、最大回撤等）

如果失败，看终端错误码：

| 错误码 | 含义 | 怎么修 |
| --- | --- | --- |
| `230001` / `230002` | App ID / Secret 错 | 检查凭证 |
| `230006` | 权限不够 | 重新申请 `im:message` 权限 |
| `230020` / `230021` | 没在群里 | 把机器人加进 chat |
| `230034` | chat_id 错 | 用浏览器地址栏确认 |

## 10. CI / 自动化场景

在 GitHub Actions 里，把 App ID / Secret / Chat ID 存进 repository secrets，然后：

```yaml
- name: Publish daily report
  env:
    FEISHU_APP_ID: ${{ secrets.FEISHU_APP_ID }}
    FEISHU_APP_SECRET: ${{ secrets.FEISHU_APP_SECRET }}
    FEISHU_CHAT_ID: ${{ secrets.FEISHU_CHAT_ID }}
  run: |
    pip install -e .
    gap publish 60_40 --period 5y
```

定时任务（cron）每天发一条。

## 进阶

### 自定义卡片标题

```bash
gap publish 60_40 --title "周五收盘组合复盘"
```

### 发到指定 chat（不用默认）

```bash
gap publish 60_40 --chat oc_other_chat_id
```

### 看卡片 JSON 结构

```bash
gap publish 60_40 --dry-run | jq .
```

JSON 字段说明（飞书 chart card schema）：

- `header.template` — `blue | green | red | ...`
- `elements[].tag` — `div | chart | table | hr`
- `chart_spec` — echarts 风格：
  - `type: line` — 折线（资金曲线）
  - `type: pie` — 饼图（当前权重）
  - 数据点 > 1000 自动降采样

### 加新图表

`src/global_allocation/feishu/card.py` 里的 `_build_equity_chart` / `_build_weights_pie` 是两个 builder。要加新图表（比如回撤曲线），照着写一个 builder，返回标准 echarts spec，然后 `build_backtest_card` 里 `elements.append(...)` 就行。

## 安全

- App Secret 等同于密码，绝不能 commit
- 配置文件建议 `chmod 600`
- CI 用 repository secrets 而不是写在 workflow 文件里
- 如果 secret 疑似泄露，飞书开放平台 → 「重置 Secret」立即吊销

## 故障排查

**机器人发了但群里看不到？**
- 检查群设置，机器人是不是被「禁言」了
- 检查消息是不是被「防骚扰」规则拦了

**卡片显示「加载中」？**
- 飞书 chart card 是异步渲染的，等几秒
- 数据点太多（>1000 已被我们降采样，不会触发）
- 检查 chart_spec 格式：飞书对 echarts spec 是子集，不是所有 echarts 都支持

**HTTP 500？**
- `lark-oapi` 版本不对；用 `lark-oapi>=1.2,<2`（项目里 pin 的范围）

**token 一直失败？**
- App Secret 重置过没生效？等 1 分钟缓存
- 网络问题？飞书 API 域名是 `https://open.feishu.cn`
