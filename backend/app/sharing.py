"""Offline share-text parsing and stable URL keys, without network resolution."""

import re
from urllib.parse import urlsplit, urlunsplit

from .media import extract_link, extract_share_title

MAX_BATCH_ITEMS = 20
MAX_BATCH_TEXT = 200000


def video_id_from_url(url: str) -> str | None:
    try:
        parsed = urlsplit(extract_link(url))
    except ValueError:
        return None
    match = re.search(r"/video/(\d+)", parsed.path)
    return match[1] if match else None


def link_identity(url: str) -> str:
    url = extract_link(url)
    if video_id := video_id_from_url(url):
        return f"douyin:{video_id}"
    parsed = urlsplit(url)
    return urlunsplit(("https", parsed.hostname, parsed.path.rstrip("/"), "", ""))


def parse_batch_share_text(text: str) -> list[dict]:
    if not isinstance(text, str) or len(text) > MAX_BATCH_TEXT:
        raise ValueError("批量分享内容不能超过 200000 个字符。")
    links = list(re.finditer(r'https?://[^\s<>"“”]+', text, flags=re.IGNORECASE))
    if not links:
        raise ValueError("请粘贴抖音视频分享内容或链接，每批最多 20 条。")
    if len(links) > MAX_BATCH_ITEMS:
        raise ValueError("每批最多支持 20 条链接，请分批提交。")
    items = []
    previous = 0
    for index, match in enumerate(links):
        prefix = text[previous : match.start()]
        if previous:
            # A pasted share's trailing app instructions belong to its previous
            # URL, never to the next video's title, even when shares are inline.
            prefix = re.sub(r"^\s*复制此链接.*?直接观看视频[!！]?", "", prefix, count=1, flags=re.S)
            prefix = re.sub(r"^\s*复制此链接[^\n]*(?:\n|$)", "", prefix, count=1)
        share = f"{prefix.strip()} {match[0]}".strip()
        item = dict(
            index=index,
            share_text=share,
            url=None,
            title="链接文案",
            status="ready",
            existing=None,
            duplicate_of=None,
            error=None,
        )
        try:
            if len(share) > 10000:
                raise ValueError("单条分享内容不能超过 10000 个字符。")
            item["url"] = extract_link(share)
            item["title"] = extract_share_title(share) or "链接文案"
        except ValueError as error:
            item.update(status="invalid", error=str(error))
        items.append(item)
        previous = match.end()
    return items
