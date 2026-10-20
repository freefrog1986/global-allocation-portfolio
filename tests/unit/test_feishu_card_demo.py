"""测试 src/global_allocation/portfolio/feishu_card_demo.py。

liubo 2026-10-19 拍板：给他看一张飞书卡片 Demo，列全所有支持的组件。
"""
from __future__ import annotations

import json

from global_allocation.portfolio.feishu_card_demo import (
    build_feishu_card_demo,
)


def _collect_tags(card: dict) -> set[str]:
    """递归收集卡片里出现的所有 tag。"""
    tags: set[str] = set()

    def walk(el):
        if isinstance(el, dict):
            if "tag" in el:
                tags.add(el["tag"])
            for v in el.values():
                if isinstance(v, dict):
                    walk(v)
                elif isinstance(v, list):
                    for x in v:
                        walk(x)

    if "header" in card:
        walk(card["header"])
    for el in card.get("body", {}).get("elements", []):
        walk(el)
    return tags


class TestStructure:
    def test_schema_2(self) -> None:
        card = build_feishu_card_demo()
        assert card["schema"] == "2.0"
        assert "body" in card
        assert "elements" in card["body"]

    def test_header(self) -> None:
        card = build_feishu_card_demo()
        h = card["header"]
        assert h["title"]["tag"] == "plain_text"
        assert "飞书卡片组件 Demo" in h["title"]["content"]
        assert "template" in h
        assert "icon" in h
        assert h["icon"]["token"]
        # text_tag_list
        assert len(h["text_tag_list"]) >= 1

    def test_body_has_many_elements(self) -> None:
        card = build_feishu_card_demo()
        assert len(card["body"]["elements"]) >= 20


class TestCoverage:
    """验证用到一组核心 tag，覆盖用户最关心的组件类型。"""

    EXPECTED_TAGS = {
        # Container / layout
        "div", "markdown", "column_set", "column", "collapsible_panel",
        "interactive_container", "form", "hr",
        # Display
        "person", "chart", "table",
        # Interactive
        "button", "select_static", "multi_select_static", "overflow",
        "input", "date_picker", "picker_time", "picker_datetime", "checker",
    }
    # img 不在 EXPECTED：需要先上传拿 img_key，不能用占位字符串（会被 API 拒）

    def test_all_expected_tags_present(self) -> None:
        card = build_feishu_card_demo()
        tags = _collect_tags(card)
        missing = self.EXPECTED_TAGS - tags
        assert not missing, f"缺的 tag：{missing}"


class TestSerializability:
    def test_json_serializable(self) -> None:
        card = build_feishu_card_demo()
        s = json.dumps(card, ensure_ascii=False)
        # 反解回来闭合应该对得上
        parsed = json.loads(s)
        assert parsed["schema"] == "2.0"

    def test_no_unbalanced_brackets(self) -> None:
        card = build_feishu_card_demo()
        s = json.dumps(card, ensure_ascii=False)
        # 简单配对校验
        assert s.count("{") == s.count("}")
        assert s.count("[") == s.count("]")
