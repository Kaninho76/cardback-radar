#!/usr/bin/env python3
"""Leest de nieuwsfeeds van de verzamelaarssites en schrijft feed.json.

Draait elk uur via GitHub Actions. Geen externe pakketten: alleen de
standaardbibliotheek, zodat er nooit iets kan breken door een update.
Een bron die niet reageert wordt overgeslagen, niet gefingeerd.
"""

import json
import re
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
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

# Wikimedia vraagt om een herkenbare User-Agent met een manier om contact op te nemen
WIKI_UA = "CardbackRadar/1.0 (https://github.com/Kaninho76/cardback-radar) python-urllib"

MAX_ITEMS = 45

# Wat we volgen op Wikipedia. De pageviews-API van Wikimedia is officieel,
# gratis en zonder sleutel; hij meet hoeveel mensen een onderwerp opzoeken.
# Dat is geen Google-zoekvolume, maar wel dezelfde soort aandacht — en het is
# de enige bron van dit type die je zonder betaalde koppeling mag gebruiken.
TRACK = [
    ("Marvel Legends", "Marvel_Legends"),
    ("Avengers: Doomsday", "Avengers:_Doomsday"),
    ("Masters of the Universe", "Masters_of_the_Universe"),
    ("He-Man", "He-Man"),
    ("TMNT", "Teenage_Mutant_Ninja_Turtles"),
    ("Godzilla", "Godzilla"),
    ("Kaiju", "Kaiju"),
    ("Power Rangers", "Power_Rangers"),
    ("G.I. Joe", "G.I._Joe"),
    ("Transformers", "Transformers_(franchise)"),
    ("McFarlane Toys", "McFarlane_Toys"),
    ("Actiefiguren", "Action_figure"),
]

# de pageview-cijfers lopen ongeveer twee dagen achter
LAG_DAYS = 2
RECENT_DAYS = 7
BASE_DAYS = 28
TRENDS_MAX_AGE_H = 12
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


def load_old():
    try:
        with open("feed.json", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def views(article):
    """Dagelijkse pageviews van de afgelopen weken, of None als het misgaat."""
    end = datetime.now(timezone.utc) - timedelta(days=LAG_DAYS)
    start = end - timedelta(days=BASE_DAYS + RECENT_DAYS)
    url = (
        "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
        "en.wikipedia/all-access/user/%s/daily/%s/%s"
        % (urllib.parse.quote(article, safe=""),
           start.strftime("%Y%m%d"), end.strftime("%Y%m%d"))
    )
    req = urllib.request.Request(url, headers={"User-Agent": WIKI_UA})
    with urllib.request.urlopen(req, timeout=25) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return [int(i.get("views") or 0) for i in data.get("items", [])]


def build_trends():
    """Wat wordt er de laatste week meer opgezocht dan daarvoor."""
    rows, missed = [], []
    for label, article in TRACK:
        try:
            series = views(article)
        except Exception as exc:                # noqa: BLE001
            missed.append("%s: %s" % (label, type(exc).__name__))
            print("trend overgeslagen: %s (%s)" % (label, exc), file=sys.stderr)
            time.sleep(7)
            continue

        if len(series) < RECENT_DAYS + 7:
            missed.append("%s: te weinig data" % label)
            time.sleep(7)
            continue

        recent = series[-RECENT_DAYS:]
        base = series[:-RECENT_DAYS]
        avg_recent = sum(recent) / float(len(recent))
        avg_base = sum(base) / float(len(base)) if base else 0.0
        if avg_base <= 0:
            missed.append("%s: geen basislijn" % label)
            time.sleep(7)
            continue

        rows.append({
            "label": label,
            "article": article,
            "perDay": int(round(avg_recent)),
            "change": int(round((avg_recent - avg_base) / avg_base * 100)),
        })
        time.sleep(7)   # ruim binnen de limiet van Wikimedia

    rows.sort(key=lambda r: r["change"], reverse=True)
    return {
        "updatedISO": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "window": "%d dagen tegen de %d daarvoor" % (RECENT_DAYS, BASE_DAYS),
        "rows": rows,
        "missed": missed,
    }


def trends_are_fresh(old):
    t = (old or {}).get("trends") or {}
    stamp = t.get("updatedISO")
    if not stamp or not t.get("rows"):
        return False
    try:
        when = datetime.fromisoformat(stamp)
    except ValueError:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - when) < timedelta(hours=TRENDS_MAX_AGE_H)


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
                failed.append("%s: leeg" % name)
        except urllib.error.HTTPError as exc:
            failed.append("%s: HTTP %s" % (name, exc.code))
            print("overgeslagen: %s (HTTP %s)" % (name, exc.code), file=sys.stderr)
        except Exception as exc:            # noqa: BLE001 - één stukke bron mag de rest niet slopen
            failed.append("%s: %s" % (name, type(exc).__name__))
            print("overgeslagen: %s (%s)" % (name, exc), file=sys.stderr)

    old = load_old()

    # wat we eerder hadden blijft staan als een bron vandaag niet meewerkt
    items.extend(old.get("items") or [])

    seen, unique = set(), []
    for it in items:
        key = it["url"].split("?")[0].rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        unique.append(it)

    unique.sort(key=lambda i: i["iso"] or "", reverse=True)
    unique = unique[:MAX_ITEMS]

    # pageview-cijfers veranderen maar één keer per dag; niet elk uur opnieuw halen
    if trends_are_fresh(old):
        trends = old["trends"]
        print("trends nog vers, overgenomen")
    else:
        trends = build_trends()
        print("trends bijgewerkt: %d onderwerpen" % len(trends["rows"]))

    payload = {
        "updatedISO": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sourcesOk": ok,
        "sourcesFailed": failed,
        "items": unique,
        "trends": trends,
    }

    with open("feed.json", "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)

    print("%d berichten; gelukt: %s" % (len(unique), ", ".join(ok) or "geen"))
    if failed:
        print("niet gelukt: %s" % "; ".join(failed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
