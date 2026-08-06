from astrlover.vision.tags import (
    JUNK_SEG_MIN,
    parse_tag_line,
    rating_wants,
    scrub_tag_line,
    season_wants,
)


def test_parse_new_format_with_season():
    desc = "OOTD+性感---春秋---无水印---无遮挡---针织衫,百褶裙,校园\n人物整体：一名女生站在树下。"
    tags = parse_tag_line(desc)
    assert tags["rating"] == "OOTD+性感"
    assert tags["season"] == "春秋"
    assert "针织衫" in tags["keywords"] and "校园" in tags["keywords"]


def test_parse_legacy_format_without_season():
    """旧版第二段是水印，不能被误读成季节。"""
    desc = "性感---右下角有水印---无遮挡---黑丝,高跟"
    tags = parse_tag_line(desc)
    assert tags["rating"] == "性感"
    assert tags["season"] == ""
    assert "黑丝" in tags["keywords"]
    assert "右下角有水印" in tags["keywords"]


def test_rating_alias_and_no_skip():
    assert parse_tag_line("日常---四季---无---无---街拍")["rating"] == "生活"
    # 跳级是判错：取较轻那档兜底，绝不产出 生活+露点
    assert parse_tag_line("生活+露点---夏---无---无---x")["rating"] == "生活"


def test_tag_line_at_end_also_found():
    desc = "人物整体：她坐在窗边。\n诱惑---冬---无---无---毛衣,雪景"
    tags = parse_tag_line(desc)
    assert tags["rating"] == "诱惑" and tags["season"] == "冬"


def test_scrub_removes_junk_segment_and_renames():
    junk = "显" * (JUNK_SEG_MIN + 2)
    desc = f"露点---夏---无---无---{junk}---泳池\n一名女性靠在池边。"
    out = scrub_tag_line(desc, subject_name="晚晴")
    assert junk not in out
    assert "泳池" in out
    assert "晚晴靠在池边" in out


def test_synonyms():
    assert set(rating_wants("骚")) == {"露点", "淫荡"}
    assert set(rating_wants("穿搭")) == {"OOTD"}
    assert rating_wants("不认识的词") == ()
    assert set(season_wants("换季")) == {"春", "秋"}
