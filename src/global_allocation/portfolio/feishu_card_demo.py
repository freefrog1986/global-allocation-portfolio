"""飞书卡片 所有支持组件 一览 Demo（liubo 2026-10-19 拍板）。

按 specs/lark-im-card-2.0 (larksuite/cli 文档) 拼了一张完整 Card 2.0，
把 body.elements 能塞的 tag 全部塞一遍：

  容器 / 布局
    - header (title / subtitle / template / text_tag_list / icon)
    - hr
    - div (text/plain_text + icon + fields)
    - markdown (粗 / 斜 / 链接 / @人 / <font color> / text_tag / number_tag / <hr> / 代码块 / 列表 / 引用 / 表情)
    - column_set + column (横向分栏)
    - collapsible_panel (折叠面板)
    - interactive_container (整块可点击)
    - form (表单容器 + input + select_static + date_picker + picker_datetime + picker_time + submit button)

  展示
    - img
    - person
    - chart (VChart bar + line)
    - table (text / number / options / date 多种 data_type)

  交互
    - button (primary_filled / default / danger / text 等 type，全用 behaviors)
    - select_static (下拉单选)
    - multi_select_static (下拉多选)
    - overflow (折叠按钮组)
    - input (单行 / 多行)
    - date_picker
    - picker_time
    - picker_datetime
    - checker (勾选)

业务侧不需要这些组件的话别上（liubo 2026-10-19），这只是一次"给你看看飞书都能画哪些东西"。
"""
from __future__ import annotations

import json
from datetime import date
from typing import Any

from global_allocation.feishu.credentials import FeishuCredentials, load_credentials
import urllib.request


def build_feishu_card_demo() -> dict[str, Any]:
    """构造 完整飞书卡片 Demo JSON（Card 2.0 schema）。

    Returns:
        dict[str, Any]: 直接喂给飞书 `/im/v1/messages` 的 `content` 字段。
    """
    today = date.today().isoformat()

    # ─── header (含 标题 / 副标题 / 13 种 template / 后缀标签 / icon) ───
    header = {
        "title": {"tag": "plain_text", "content": "飞书卡片组件 Demo"},
        "subtitle": {"tag": "plain_text", "content": f"Card 2.0 · {today} · liubo 2026-10-19"},
        "template": "purple",
        "icon": {"tag": "standard_icon", "token": "lark-logo_colorful"},
        "text_tag_list": [
            {"tag": "text_tag", "text": {"tag": "plain_text", "content": "22 个 tag"}, "color": "blue"},
            {"tag": "text_tag", "text": {"tag": "plain_text", "content": "Demo"}, "color": "violet"},
        ],
    }

    elements: list[dict[str, Any]] = []

    # ─── 标签 helper：每个元素前面插一句"这是 X"的小灰字，让 liubo 一眼看清哪是哪 ───
    def _label(name: str, desc: str = "") -> dict[str, Any]:
        suffix = f"：{desc}" if desc else ""
        return {
            "tag": "markdown",
            "content": f"**⬇ 这是 `{name}`**{suffix}",
        }

    # ─────────── Section 0: 顶层 header ───────────
    elements.append(_label(
        "header",
        "卡片最顶部整条紫色横条 = template=purple；"
        "左下小图标 = icon (standard_icon)；"
        "右上两个彩色小标签 = text_tag_list；"
        "主标题 + 副标题 = title + subtitle。"
        "header 在卡片根 `header` 键下，不在 body.elements 里，"
        "全卡只能有一个。",
    ))
    elements.append({"tag": "hr"})

    # ─────────── Section 1: 容器 + 布局 ───────────
    elements.append(_label(
        "hr",
        "分割线，没属性。最朴素。",
    ))
    elements.append(_label(
        "div · 纯文本 + icon",
        "单行 plain_text 文本，前面带个小图标 (standard_icon)。",
    ))
    elements.append({
        "tag": "div",
        "text": {"tag": "plain_text", "content": "div 示例：plain_text 行，14px 正文"},
        "icon": {"tag": "standard_icon", "token": "chat_outlined", "color": "blue"},
    })
    elements.append(_label(
        "div · lark_md 彩色",
        "text.tag 切到 lark_md 后能写 <font color=...>，plain_text 不解析。",
    ))
    elements.append({
        "tag": "div",
        "text": {"tag": "lark_md", "content": "div 示例：<font color='red'>红色</font> lark_md 富文本"},
    })
    elements.append(_label(
        "div · fields 多列 label-value",
        "4 个 is_short=true 的字段会两两并排。加粗要 text.tag=lark_md。",
    ))
    elements.append({
        "tag": "div",
        "fields": [
            {"is_short": True, "text": {"tag": "lark_md", "content": "**基金代码**：006327"}},
            {"is_short": True, "text": {"tag": "lark_md", "content": "**持仓**：2.00 仓"}},
            {"is_short": True, "text": {"tag": "lark_md", "content": "**累计成本**：10,000 CNY"}},
            {"is_short": True, "text": {"tag": "lark_md", "content": "**类别上限**：30%"}},
        ],
    })

    # ─── markdown（语法一锅炖）───
    elements.append(_label(
        "markdown",
        "富文本：粗/斜/删除/链接/[font color]/text_tag/number_tag/@人/引用/列表/代码/表情。",
    ))
    elements.append({
        "tag": "markdown",
        "content": (
            "**markdown 富文本** — "
            "*斜体*  ~~删除线~~  `inline code`\n\n"
            "[飞书开放平台](https://open.feishu.cn) "
            "<font color='red'>红色</font> · "
            "<font color='blue'>蓝色</font> · "
            "<text_tag color='blue'>蓝色 tag</text_tag> "
            "<text_tag color='green'>绿色 tag</text_tag> "
            "<text_tag color='orange'>橙色 tag</text_tag>\n\n"
            "<number_tag background_color='blue' font_color='white'>9</number_tag> 张卡片 · "
            "<at id=all></at> 群成员 @所有人\n\n"
            "> 引用块：Carbon 1.0 → Card 2.0 全面升级，支持 schema 2.0\n\n"
            "- 无序 1\n- 无序 2\n  - 嵌套 2.1\n  - 嵌套 2.2\n\n"
            "1. 有序 A\n2. 有序 B\n\n"
            "```python\n# 代码块\ndef hello():\n    return 'hi'\n```\n\n"
            ":DONE: :OK: 飞书表情"
        ),
    })

    # ─── column_set + column（横向分栏）───
    elements.append(_label(
        "column_set + column",
        "横向分栏：3 个 column，weight 决定宽度比 (1:2:1)。"
        "直接子节点只能是 column，不能 column_set 套 column_set。",
    ))
    elements.append({
        "tag": "column_set",
        "flex_mode": "none",
        "columns": [
            {"tag": "column", "width": "weighted", "weight": 1, "vertical_align": "center",
             "elements": [
                 {"tag": "markdown", "content": (
                     "**左列**\n\n"
                     "<font color='grey'>weight=1</font>"
                 )},
             ]},
            {"tag": "column", "width": "weighted", "weight": 2, "vertical_align": "center",
             "elements": [
                 {"tag": "markdown", "content": (
                     "**中列（更宽）**\n\n"
                     "<font color='grey'>weight=2</font>"
                 )},
             ]},
            {"tag": "column", "width": "weighted", "weight": 1, "vertical_align": "center",
             "elements": [
                 {"tag": "markdown", "content": (
                     "**右列**\n\n"
                     "<font color='grey'>weight=1</font>"
                 )},
             ]},
        ],
    })

    # ─── collapsible_panel ───
    elements.append(_label(
        "collapsible_panel",
        "折叠面板：默认收起，点标题展开。装备注、长说明等次要信息。",
    ))
    elements.append({
        "tag": "collapsible_panel",
        "expanded": False,
        "header": {
            "title": {"tag": "plain_text", "content": "折叠面板 — 点开看备注"},
        },
        "elements": [
            {"tag": "markdown", "content": (
                "折叠收起的内容。\n\n"
                "适合放：备注、长说明、字段对、辅助文案等次要信息，避免主卡信息过载。"
            )},
        ],
        "border": {"color": "grey", "corner_radius": "8px"},
    })

    # ─── interactive_container（整块可点击）───
    elements.append(_label(
        "interactive_container",
        "整块可点击的容器（蓝边 + 圆角）。点整块触发 callback，"
        "如果内部有 button 优先响应内部组件。",
    ))
    elements.append({
        "tag": "interactive_container",
        "has_border": True,
        "border_color": "blue",
        "corner_radius": "8px",
        "padding": "8px 12px",
        "behaviors": [{"type": "callback", "value": {"action": "demo_card_clicked"}}],
        "elements": [
            {"tag": "markdown", "content": (
                "<font color='grey'>点这一整块容器会触发 callback，"
                "服务端收到 `action.tag='interactive_container'` + value</font>"
            )},
        ],
    })

    elements.append({"tag": "hr"})

    # ─────────── Section 2: 展示 ───────────
    elements.append(_label(
        "img",
        "图片元素。需要先调 [上传图片接口]("
        "https://open.feishu.cn/document/server-side/im/v1/image/create) 拿 `img_key` 再渲染。"
        "卡片有合法 img_key 时会自动按 corner_radius 圆角、aspect_ratio 比例渲染，可点开放大。\n\n"
        "本卡没传 img_key（占位字符串会被飞书拒 400），所以这里不渲染实图，"
        "结构示例：\n"
        "```json\n"
        "{ \"tag\": \"img\", \"img_key\": \"<上传后拿到>\", \"alt\": { \"tag\": \"plain_text\", \"content\": \"hover\" } }\n"
        "```",
    ))

    # ─── person ───
    elements.append(_label(
        "person",
        "人员头像 + 姓名。user_id 填 open_id / union_id / user_id 之一。"
        "左边 medium normal 样式，右边 small capsule 胶囊样式。",
    ))
    elements.append({
        "tag": "column_set",
        "columns": [
            {"tag": "column", "width": "weighted", "weight": 1,
             "elements": [
                 {"tag": "markdown", "content": "**person · normal**"},
                 {"tag": "person", "user_id": "ou_d802604a6018c63ffb5ee8a57b9138fc", "size": "medium", "show_name": True, "show_avatar": True, "style": "normal"},
             ]},
            {"tag": "column", "width": "weighted", "weight": 1,
             "elements": [
                 {"tag": "markdown", "content": "**person · capsule**"},
                 {"tag": "person", "user_id": "ou_d802604a6018c63ffb5ee8a57b9138fc", "size": "small", "show_name": True, "show_avatar": True, "style": "capsule"},
             ]},
        ],
    })

    # ─── chart（柱状图）───
    elements.append(_label(
        "chart · 柱状图 (bar)",
        "VChart spec：type=bar, xField/day, yField/amount。"
        "aspect_ratio 16:9。",
    ))
    elements.append({
        "tag": "chart",
        "chart_spec": {
            "type": "bar",
            "title": {"text": "近 5 日定投"},
            "data": {"values": [
                {"day": "周一", "amount": 100},
                {"day": "周二", "amount": 80},
                {"day": "周三", "amount": 120},
                {"day": "周四", "amount": 95},
                {"day": "周五", "amount": 150},
            ]},
            "xField": "day",
            "yField": "amount",
            "color": ["#5B8FF9"],
        },
        "aspect_ratio": "16:9",
    })
    elements.append(_label(
        "chart · 折线图 (line)",
        "VChart spec：type=line。",
    ))
    elements.append({
        "tag": "chart",
        "chart_spec": {
            "type": "line",
            "title": {"text": "组合净值"},
            "data": {"values": [
                {"day": "10-13", "value": 1.020},
                {"day": "10-14", "value": 1.018},
                {"day": "10-15", "value": 1.025},
                {"day": "10-16", "value": 1.031},
                {"day": "10-17", "value": 1.028},
            ]},
            "xField": "day",
            "yField": "value",
        },
        "aspect_ratio": "16:9",
    })

    # ─── table ───
    elements.append(_label(
        "table",
        "多列数据表。列 data_type 决定行值结构："
        "text/lark_md/number/options/persons/date/markdown。"
        "只能在根 body.elements，不能被任何容器嵌套。",
    ))
    elements.append({
        "tag": "table",
        "page_size": 5,
        "columns": [
            {"name": "code", "display_name": "代码", "data_type": "text"},
            {"name": "name", "display_name": "基金", "data_type": "text"},
            {"name": "qty", "display_name": "持仓(份)", "data_type": "number", "format": {"precision": 2, "separator": True}},
            {"name": "state", "display_name": "状态", "data_type": "options"},
            {"name": "date", "display_name": "调仓日期", "data_type": "date", "date_format": "YYYY-MM-DD"},
        ],
        "rows": [
            {"code": "006327", "name": "港股科技互联", "qty": 5000, "state": [{"text": "持有", "color": "blue"}], "date": 1760659200000},
            {"code": "006809", "name": "港股银行", "qty": 1010, "state": [{"text": "持有", "color": "blue"}], "date": 1761004800000},
            {"code": "010989", "name": "中证房地产", "qty": 5000, "state": [{"text": "新买", "color": "green"}], "date": 1761264000000},
        ],
    })

    elements.append({"tag": "hr"})

    # ─────────── Section 3: 交互 ───────────
    def _btn(label: str, btype: str) -> dict[str, Any]:
        return {"tag": "button", "text": {"tag": "plain_text", "content": label},
                "type": btype, "behaviors": [{"type": "callback", "value": {"k": btype}}]}

    elements.append(_label(
        "button · 6 种 type 之一",
        "type 枚举：primary_filled / default / danger_filled / danger / primary / text。"
        "全用 behaviors=[callback]。全卡只能有一个 primary_filled 主按钮。",
    ))
    elements.append({
        "tag": "column_set",
        "columns": [
            {"tag": "column", "width": "weighted", "weight": 1, "elements": [_btn("primary_filled", "primary_filled")]},
            {"tag": "column", "width": "weighted", "weight": 1, "elements": [_btn("default", "default")]},
            {"tag": "column", "width": "weighted", "weight": 1, "elements": [_btn("danger_filled", "danger_filled")]},
        ],
    })
    elements.append(_label(
        "button · 另 3 种 type",
        "danger（红描边）/ primary（蓝描边）/ text（无框纯文字）。",
    ))
    elements.append({
        "tag": "column_set",
        "columns": [
            {"tag": "column", "width": "weighted", "weight": 1, "elements": [
                {"tag": "button", "text": {"tag": "plain_text", "content": "danger"},
                 "type": "danger", "behaviors": [{"type": "callback", "value": {"k": "danger"}}]}
            ]},
            {"tag": "column", "width": "weighted", "weight": 1, "elements": [
                {"tag": "button", "text": {"tag": "plain_text", "content": "primary"},
                 "type": "primary", "behaviors": [{"type": "callback", "value": {"k": "primary"}}]}
            ]},
            {"tag": "column", "width": "weighted", "weight": 1, "elements": [
                {"tag": "button", "text": {"tag": "plain_text", "content": "text"},
                 "type": "text", "behaviors": [{"type": "callback", "value": {"k": "text"}}]}
            ]},
        ],
    })
    elements.append(_label(
        "button · 跳转链接 (open_url)",
        "behaviors 里 type=open_url + default_url，跳外链。"
        "可以跟 callback 同数组共存。",
    ))
    elements.append({
        "tag": "button",
        "text": {"tag": "plain_text", "content": "open_url：跳转飞书开放平台"},
        "type": "primary_filled",
        "behaviors": [{"type": "open_url", "default_url": "https://open.feishu.cn"}],
        "width": "fill",
    })

    # ─── select_static ───
    elements.append(_label(
        "select_static",
        "下拉单选。options[].value 不可重复，"
        "回调 `action.tag='select_static'` + `action.option`。",
    ))
    elements.append({
        "tag": "select_static",
        "placeholder": {"tag": "plain_text", "content": "请选择基金"},
        "options": [
            {"text": {"tag": "plain_text", "content": "006327 港股科技互联"}, "value": "006327"},
            {"text": {"tag": "plain_text", "content": "006809 港股银行"}, "value": "006809"},
            {"text": {"tag": "plain_text", "content": "457001 亚太除日本"}, "value": "457001"},
            {"text": {"tag": "plain_text", "content": "010989 中证房地产"}, "value": "010989"},
        ],
        "behaviors": [{"type": "callback", "value": {"k": "select_static"}}],
    })

    # ─── multi_select_static ───
    elements.append(_label(
        "multi_select_static",
        "下拉多选。默认值用 `selected_values=[...]`（数组，不是 initial_index）。",
    ))
    elements.append({
        "tag": "multi_select_static",
        "placeholder": {"tag": "plain_text", "content": "请选择标签（可多选）"},
        "options": [
            {"text": {"tag": "plain_text", "content": "定投"}, "value": "dca"},
            {"text": {"tag": "plain_text", "content": "调仓"}, "value": "rebal"},
            {"text": {"tag": "plain_text", "content": "止盈"}, "value": "take_profit"},
            {"text": {"tag": "plain_text", "content": "抄底"}, "value": "buy_dip"},
        ],
        "selected_values": ["dca"],
        "behaviors": [{"type": "callback", "value": {"k": "multi_select_static"}}],
    })

    # ─── overflow ───
    elements.append(_label(
        "overflow",
        "折叠按钮组。操作多时收纳到一个按钮里，点开展开。"
        "每个 options[].value 要唯一，否则回传无法区分。",
    ))
    elements.append({
        "tag": "overflow",
        "options": [
            {"text": {"tag": "plain_text", "content": "加仓 1 仓"}, "value": "buy_1"},
            {"text": {"tag": "plain_text", "content": "减仓 1 仓"}, "value": "sell_1"},
            {"text": {"tag": "plain_text", "content": "调仓 1 仓"}, "value": "swap_1"},
            {"text": {"tag": "plain_text", "content": "清仓"}, "value": "close"},
        ],
        "behaviors": [{"type": "callback", "value": {"k": "overflow"}}],
    })

    # ─── input ───
    elements.append(_label(
        "input",
        "文本输入框。input_type: text / multiline_text / password。"
        "label 字段是表单外 label，label_position: top / left。",
    ))
    elements.append({
        "tag": "input",
        "name": "demo_input",
        "placeholder": {"tag": "plain_text", "content": "单行 input"},
        "label": {"tag": "plain_text", "content": "单行"},
        "behaviors": [{"type": "callback", "value": {"k": "input"}}],
    })

    # ─── checker ───
    elements.append(_label(
        "checker",
        "勾选框（任务 todo 场景）。未配 behaviors 时只本地勾选不会回调。",
    ))
    elements.append({
        "tag": "checker",
        "name": "todo_demo",
        "checked": False,
        "text": {"tag": "plain_text", "content": "本次 demo 看完后，告诉我哪些 tag 有用"},
        "behaviors": [{"type": "callback", "value": {"k": "checker_todo"}}],
    })

    # ─── date_picker / picker_time / picker_datetime 并排 ───
    elements.append(_label(
        "date_picker / picker_time / picker_datetime",
        "日期/时间/日期+时间 三种 picker。"
        "回调时区是用户当前时区，提醒用户时区语境。",
    ))
    elements.append({
        "tag": "column_set",
        "columns": [
            {"tag": "column", "width": "weighted", "weight": 1, "elements": [
                {"tag": "markdown", "content": "**date_picker**"},
                {"tag": "date_picker", "placeholder": {"tag": "plain_text", "content": "选择日期"},
                 "initial_date": today, "behaviors": [{"type": "callback", "value": {"k": "date"}}]},
            ]},
            {"tag": "column", "width": "weighted", "weight": 1, "elements": [
                {"tag": "markdown", "content": "**picker_time**"},
                {"tag": "picker_time", "placeholder": {"tag": "plain_text", "content": "选择时间"},
                 "initial_time": "09:30", "behaviors": [{"type": "callback", "value": {"k": "time"}}]},
            ]},
            {"tag": "column", "width": "weighted", "weight": 1, "elements": [
                {"tag": "markdown", "content": "**picker_datetime**"},
                {"tag": "picker_datetime", "placeholder": {"tag": "plain_text", "content": "选日期+时间"},
                 "initial_datetime": f"{today} 09:30", "behaviors": [{"type": "callback", "value": {"k": "dt"}}]},
            ]},
        ],
    })

    elements.append({"tag": "hr"})

    # ─────────── Section 4: form（批量提交） ───────────
    elements.append(_label(
        "form",
        "表单容器：批量录入后一次提交。"
        "form 本身只能在根 body.elements 下；"
        "内部组件 name 必填且全局唯一；按钮用 form_action_type=submit/reset 不用 behaviors。",
    ))
    elements.append({
        "tag": "form",
        "name": "demo_form",
        "elements": [
            {"tag": "input", "name": "comment", "placeholder": {"tag": "plain_text", "content": "想说点什么"},
             "label": {"tag": "plain_text", "content": "留言"}},
            {"tag": "input", "name": "note_multi", "placeholder": {"tag": "plain_text", "content": "多行备注"},
             "label": {"tag": "plain_text", "content": "多行"}, "input_type": "multiline_text", "rows": 3},
            {"tag": "select_static", "name": "fund_choice", "required": True,
             "placeholder": {"tag": "plain_text", "content": "必选：调仓基金"},
             "options": [
                 {"text": {"tag": "plain_text", "content": "006327"}, "value": "006327"},
                 {"text": {"tag": "plain_text", "content": "006809"}, "value": "006809"},
                 {"text": {"tag": "plain_text", "content": "010989"}, "value": "010989"},
             ]},
            {"tag": "date_picker", "name": "trade_date",
             "placeholder": {"tag": "plain_text", "content": "必选：成交日期"},
             "initial_date": today},
            {"tag": "picker_time", "name": "trade_time",
             "placeholder": {"tag": "plain_text", "content": "成交时间"},
             "initial_time": "09:30"},
            {"tag": "checker", "name": "confirm",
             "checked": False, "text": {"tag": "plain_text", "content": "我已确认基金代码"}},
            {"tag": "button", "name": "submit_btn", "form_action_type": "submit",
             "text": {"tag": "plain_text", "content": "提交表单"},
             "type": "primary_filled", "width": "fill"},
            {"tag": "button", "name": "reset_btn", "form_action_type": "reset",
             "text": {"tag": "plain_text", "content": "重置"},
             "type": "default"},
        ],
    })

    elements.append({"tag": "hr"})

    # ─── footer 风格的 note（卡片根不支持 footer 字段，用 markdown 灰色补）───
    elements.append({
        "tag": "markdown",
        "content": (
            "<font color='grey'>"
            "liubo 2026-10-19 拍板：这张卡只是给你看看飞书都能塞哪些组件，"
            "业务侧不需要就不会上。完整 spec 看 "
            "[larksuite/cli card docs](https://github.com/larksuite/cli/tree/main/skills/lark-im/references/card/components)"
            "</font>"
        ),
    })

    return {
        "schema": "2.0",
        "config": {"width_mode": "default", "enable_forward": True},
        "header": header,
        "body": {
            "direction": "vertical",
            "padding": "12px 12px 20px 12px",
            "elements": elements,
        },
    }


# ─── 发送 ───


def _post_card(card: dict[str, Any], credentials: FeishuCredentials) -> str:
    """调飞书 OpenAPI 发卡，返回 message_id。和 publisher._send_card 思路一致。"""
    import json
    import urllib.request

    # token
    token_req = urllib.request.Request(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        data=json.dumps({"app_id": credentials.app_id, "app_secret": credentials.app_secret}).encode(),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(token_req, timeout=10) as r:
        token = json.loads(r.read())["tenant_access_token"]

    # send
    url = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id"
    payload = {
        "receive_id": credentials.chat_id,
        "msg_type": "interactive",
        "content": json.dumps(card, ensure_ascii=False),
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        body = json.loads(r.read())
    if body.get("code", 0) != 0:
        raise RuntimeError(f"飞书 API 返回错误: {body.get('code')} {body.get('msg')}")
    return body.get("data", {}).get("message_id", "")


def send_feishu_card_demo(
    credentials: FeishuCredentials | None = None,
    dry_run: bool = False,
) -> str:
    """发飞书卡片 Demo 到默认 chat。

    Args:
        credentials: 飞书凭证（None → 走 env/file）。
        dry_run: True → 返回 JSON 字符串，不真发。

    Returns:
        dry_run=True → 卡片 JSON 字符串
        dry_run=False → message_id
    """
    if credentials is None:
        credentials = load_credentials()

    card = build_feishu_card_demo()
    if dry_run:
        return json.dumps(card, ensure_ascii=False)

    return _post_card(card, credentials)


__all__ = ["build_feishu_card_demo", "send_feishu_card_demo"]
