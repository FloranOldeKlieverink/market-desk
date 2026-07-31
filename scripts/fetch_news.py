"""RSS adapters. Every source emits the same shape, so adding one is one config block."""
import re, sys, html
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from common import fetch, settings, write_json, log, guard, stamp

STRIP = re.compile(r"<[^>]+>")


def clean(s, limit=260):
    """Decode to a stable string first, then strip.

    Feeds are routinely double-encoded: the XML parser decodes one layer, leaving
    HTML entities behind. Stripping before decoding lets markup that was encoded
    twice survive as live tags, which then reach the browser. Decode to a fixed
    point, strip, then remove any angle brackets that are still standing."""
    if not s:
        return ""
    for _ in range(3):
        prev = s
        s = html.unescape(s)
        if s == prev:
            break
    s = STRIP.sub(" ", s)
    s = s.replace("<", " ").replace(">", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s[:limit]


def parse_feed(raw):
    """Handles RSS 2.0 and Atom without caring which it was given."""
    root = ET.fromstring(raw)
    items = root.findall(".//item")
    if items:
        out = []
        for it in items:
            out.append({
                "title": clean(it.findtext("title"), 200),
                "link": (it.findtext("link") or "").strip(),
                "summary": clean(it.findtext("description")),
                "published": (it.findtext("pubDate") or "").strip(),
            })
        return out
    ns = {"a": "http://www.w3.org/2005/Atom"}
    out = []
    for e in root.findall(".//a:entry", ns):
        link = e.find("a:link", ns)
        out.append({
            "title": clean(e.findtext("a:title", default="", namespaces=ns), 200),
            "link": (link.get("href") if link is not None else "") or "",
            "summary": clean(e.findtext("a:summary", default="", namespaces=ns)),
            "published": (e.findtext("a:updated", default="", namespaces=ns) or "").strip(),
        })
    return out


def to_iso(s):
    try:
        return parsedate_to_datetime(s).astimezone().isoformat(timespec="seconds")
    except Exception:                               # noqa: BLE001
        return s or ""


def tag_story(story, cfg):
    """Ticker tags from the instrument list, theme tags from the keyword lists."""
    text = f"{story['title']} {story['summary']}".lower()
    tickers = []
    for i in cfg["instruments"]:
        names = {i["ticker"].lower(), i["name"].lower()}
        first = i["name"].split()[0].lower()
        if len(first) > 3:
            names.add(first)
        if any(re.search(rf"\b{re.escape(n)}\b", text) for n in names if n):
            tickers.append(i["ticker"])
    themes = [t for t, words in cfg.get("themes", {}).items()
              if any(re.search(rf"\b{re.escape(w)}\b", text) for w in words)]
    return tickers, themes


@guard("news")
def main():
    cfg = settings()
    stories, sources, seen = [], [], set()
    for src in cfg["news_sources"]:
        if not src.get("enabled", True):
            continue
        sources.append({"id": src["id"], "label": src["label"], "paywall": src.get("paywall", False)})
        urls = []
        if src.get("per_ticker"):
            field = src.get("symbol_field", "alphavantage")
            for i in cfg["instruments"]:
                if i.get(field):
                    urls.append(src["url"].replace("{SYMBOL}", i[field]))
        else:
            urls.append(src["url"])
        got = 0
        for u in urls:
            try:
                for it in parse_feed(fetch(u))[:25]:
                    key = it["title"].lower()[:90]
                    if not it["title"] or key in seen:
                        continue
                    seen.add(key)
                    tickers, themes = tag_story(it, cfg)
                    stories.append({"src": src["id"], "h": it["title"], "d": it["summary"],
                                    "url": it["link"], "at": to_iso(it["published"]),
                                    "tags": tickers, "th": themes})
                    got += 1
            except Exception as e:                  # noqa: BLE001
                log(f"   {src['id']} feed failed ({u[:60]}): {type(e).__name__}")
        log(f"   {src['id']}: {got} stories")
    if not stories:
        raise RuntimeError("no stories from any enabled source")
    stories.sort(key=lambda s: s["at"], reverse=True)
    write_json("news.json", {"updated": stamp(), "sources": sources, "stories": stories[:120]})
    return True


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
