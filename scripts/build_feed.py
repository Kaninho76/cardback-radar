#!/usr/bin/env python3
"""Leest de nieuwsfeeds van de verzamelaarssites en schrijft feed.json.

Draait elk uur via GitHub Actions. Geen externe pakketten: alleen de
standaardbibliotheek, zodat er nooit iets kan breken door een update.
Een bron die niet reageert wordt overgeslagen, niet gefingeerd.
"""

import json
import re
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

FEEDS = [
    ("Toy Hype USA", "https://toyhypeusa.com/feed/"),
    ("Bleeding Cool", "https://bleedingcool.com/collectibles/feed/"),
    ("Marvel Toy News", "https://marveltoynews.com/feed/"),
    ("The Toyark", "https://www.toyark.com/feed/"),
    ("The Fwoosh", "https://thefwoosh.com/feed/"),
    ("Toy Habits", "https://toyhabits.com/feed/"),
    ("Action Figure Insider", "https://www.actionfigureinsider.com/feed/"),
]

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

MAX_ITEMS = 45
TAGS = re.compile(r"<[^>]+>")
WS = re.compile(r"\s+")


def clean(text):
    if not text:
        return ""
    text = TAGS.sub(" ", text)
    text = (text.replace("&amp;", "&").replace("&#8217;", "'")
                .replace("&#8216;", "'").replace("&#8220;", '"')
                .replace("&#8221;", '"').replace("&#8211;", "–")
                .replace("&#038;", "&").replace("&quot;", '"')
                .replace("&nbsp;", " ").replace("&lt;", "<").replace("&gt;", ">"))
    return WS.sub(" ", text).strip()


def parse_date(raw):
    if not raw:
        return None
    raw = raw.strip()
    try:
        d = parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError):
        try:
            d = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d is None:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def strip_ns(tag):
    return tag.split("}", 1)[-1] if "}" in tag else tag


def read_feed(name, url):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
    })
    with urllib.request.urlopen(req, timeout=25) as resp:
        raw = resp.read()

    root = ET.fromstring(raw)
    out = []

    nodes = [n for n in root.iter() if strip_ns(n.tag) in ("item", "entry")]
    for node in nodes:
        title = link = date_raw = None
        for child in node:
            tag = strip_ns(child.tag)
            if tag == "title" and title is None:
                title = clean(child.text or "")
            elif tag == "link" and link is None:
                href = child.get("href")
                link = href if href else clean(child.text or "")
            elif tag in ("pubDate", "published", "updated", "date") and date_raw is None:
                date_raw = child.text
        if not title or not link:
            continue
        when = parse_date(date_raw)
        out.append({
            "title": title[:180],
            "url": link.strip(),
            "source": name,
            "iso": when.isoformat() if when else None,
        })
    return out


def main():
    items, ok, failed = [], [], []

    for name, url in FEEDS:
        try:
            got = read_feed(name, url)
            if got:
                items.extend(got[:12])
                ok.append(name)
            else:
                failed.append(name)
        except Exception as exc:            # noqa: BLE001 - één stukke bron mag de rest niet slopen
            failed.append(name)
            print("overgeslagen: %s (%s)" % (name, exc), file=sys.stderr)

    seen, unique = set(), []
    for it in items:
        key = it["url"].split("?")[0].rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        unique.append(it)

    unique.sort(key=lambda i: i["iso"] or "", reverse=True)
    unique = unique[:MAX_ITEMS]

    payload = {
        "updatedISO": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sourcesOk": ok,
        "sourcesFailed": failed,
        "items": unique,
    }

    if not unique:
        print("geen enkele bron gaf items terug — feed.json niet overschreven", file=sys.stderr)
        return 1

    with open("feed.json", "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)

    print("%d berichten uit %d bronnen" % (len(unique), len(ok)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
