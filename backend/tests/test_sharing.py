import pytest
from app import sharing


def test_batch_splits_full_shares_without_previous_clipboard_instructions():
    text = (
        "0.71 :1pm b@n.qR EHv:/ 07/18 学就完了，祝大家都是表达高手 # 表达 "
        "https://v.douyin.com/BCVusZue21g/ 复制此链接，打开Dou音搜索，直接观看视频！\n"
        "2.82 SYM:/ 04/15 :6pm y@g.oD 我宁愿痛苦，我不要麻木 "
        "https://v.douyin.com/YxDhnVDo_AU/ 复制此链接，打开Dou音搜索，直接观看视频！"
    )
    items = sharing.parse_batch_share_text(text)
    assert [item["title"] for item in items] == ["学就完了，祝大家都是表达高手", "我宁愿痛苦，我不要麻木"]
    assert [item["status"] for item in items] == ["ready", "ready"]
    assert "复制此链接" not in items[1]["share_text"]
    assert items[0]["index"] == 0 and items[1]["index"] == 1


def test_invalid_link_is_an_independent_preview_row():
    items = sharing.parse_batch_share_text("无效 https://example.com/a\n有效标题 https://v.douyin.com/AbC/")
    assert len(items) == 2
    assert items[0]["status"] == "invalid" and items[0]["error"]
    assert items[1]["status"] == "ready" and items[1]["title"] == "有效标题"
    assert items[1]["url"] == "https://v.douyin.com/AbC/"


def test_plain_links_and_twenty_item_boundary():
    text = "\n".join(f"https://v.douyin.com/code{i}/" for i in range(20))
    assert len(sharing.parse_batch_share_text(text)) == 20
    with pytest.raises(ValueError, match="20"):
        sharing.parse_batch_share_text(text + "\nhttps://v.douyin.com/extra/")


@pytest.mark.parametrize("text", ["", "没有链接", "x" * 200001])
def test_invalid_batch_text_is_bounded(text):
    with pytest.raises(ValueError):
        sharing.parse_batch_share_text(text)


def test_local_identity_matches_video_pages_and_removes_tracking_but_preserves_short_code_case():
    assert sharing.link_identity("https://www.douyin.com/video/123/?from=share") == "douyin:123"
    assert sharing.link_identity("https://www.iesdouyin.com/share/video/123/") == "douyin:123"
    assert sharing.video_id_from_url("https://www.douyin.com/video/123") == "123"
    assert sharing.video_id_from_url("https://v.douyin.com/AbC/") is None
    assert sharing.video_id_from_url("https://evil.test/video/123") is None
    assert sharing.link_identity("https://v.douyin.com/AbC/?from=share") == sharing.link_identity(
        "https://v.douyin.com/AbC"
    )
    assert sharing.link_identity("https://v.douyin.com/abc/") != sharing.link_identity(
        "https://v.douyin.com/AbC/"
    )
