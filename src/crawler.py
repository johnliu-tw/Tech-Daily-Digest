"""
crawler.py - 通用內容抓取器，支援三種模式：
  - rss     : RSS / Atom feed（feedparser）
  - sitemap : XML sitemap，含 Google News sitemap 格式
  - web     : 通用網頁（trafilatura 自動提取，相當於瀏覽器閱讀模式）

日期過濾策略（多層 fallback）：
  1. Feed / sitemap 的原生時間欄位
  2. trafilatura 提取的 metadata 日期
  3. HTML 中的 JSON-LD / OpenGraph / <time> 標籤
  4. URL 中的日期樣式（/2025/02/21/、/20250221/ 等）
"""

import json
import logging
import re
import time as time_module
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from typing import Optional
from urllib.parse import urljoin, urlparse

import feedparser
import requests
import trafilatura
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

logger = logging.getLogger(__name__)

# ── 常數 ──────────────────────────────────────────────────────────────────────

# XML sitemap namespace
NS = {
    "sm":    "http://www.sitemaps.org/schemas/sitemap/0.9",
    "news":  "http://www.google.com/schemas/sitemap-news/0.9",
    "image": "http://www.google.com/schemas/sitemap-image/1.1",
}

# URL 中常見的日期樣式，用於最後 fallback
_DATE_IN_URL = re.compile(
    r"/(\d{4})[/-](\d{2})[/-](\d{2})/"  # /2025/02/21/  or  /2025-02-21/
    r"|/(\d{8})/"                         # /20250221/
)


# ── 工具函式 ──────────────────────────────────────────────────────────────────

def _make_session(user_agent: str, timeout: int) -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": user_agent})
    s.request = lambda method, url, **kw: requests.Session.request(
        s, method, url, timeout=kw.pop("timeout", timeout), **kw
    )
    return s


def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_date_str(s: str) -> Optional[datetime]:
    """將任意日期字串解析為 UTC datetime"""
    try:
        return _to_utc(dateparser.parse(s))
    except Exception:
        return None


def _date_from_url(url: str) -> Optional[datetime]:
    """從 URL 路徑提取日期（最後 fallback）"""
    m = _DATE_IN_URL.search(url)
    if not m:
        return None
    try:
        if m.group(1):  # /YYYY/MM/DD/ 格式
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        else:           # /YYYYMMDD/ 格式
            raw = m.group(4)
            y, mo, d = int(raw[:4]), int(raw[4:6]), int(raw[6:8])
        return datetime(y, mo, d, tzinfo=timezone.utc)
    except Exception:
        return None


def _extract_date_from_html(html: str, url: str) -> Optional[datetime]:
    """
    多層策略從 HTML 提取日期：
    1. JSON-LD (datePublished / dateModified)
    2. OpenGraph (article:published_time)
    3. <time datetime="..."> 標籤
    4. URL 中的日期樣式
    """
    try:
        soup = BeautifulSoup(html, "lxml")

        # 1. JSON-LD
        for tag in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(tag.string or "")
                if isinstance(data, list):
                    data = data[0]
                for field in ("datePublished", "dateModified", "dateCreated"):
                    if field in data:
                        dt = _parse_date_str(data[field])
                        if dt:
                            return dt
            except Exception:
                pass

        # 2. OpenGraph / meta tags
        for prop in ("article:published_time", "article:modified_time",
                     "og:updated_time", "date", "pubdate", "DC.date"):
            tag = soup.find("meta", attrs={"property": prop}) or \
                  soup.find("meta", attrs={"name": prop})
            if tag and tag.get("content"):
                dt = _parse_date_str(tag["content"])
                if dt:
                    return dt

        # 3. <time> 標籤
        for time_tag in soup.find_all("time"):
            dt_attr = time_tag.get("datetime") or time_tag.get_text(strip=True)
            if dt_attr:
                dt = _parse_date_str(dt_attr)
                if dt:
                    return dt

    except Exception:
        pass

    # 4. URL fallback
    return _date_from_url(url)


def _html_to_text(html: str, max_chars: int) -> str:
    """快速 HTML → 純文字，限制長度"""
    try:
        soup = BeautifulSoup(html, "lxml")
        return soup.get_text(" ", strip=True)[:max_chars]
    except Exception:
        return html[:max_chars]


def _extract_article_links(html: str, base_url: str, selector: Optional[str]) -> list[str]:
    """
    從列表頁找文章連結。
    - 優先使用使用者指定的 CSS selector
    - 否則用啟發式方法：找 <article>/<h2>/<h3>/<h4> 內的 <a>，
      排除導覽、footer、標籤/分類頁等雜訊連結
    """
    try:
        soup = BeautifulSoup(html, "lxml")
        base_domain = urlparse(base_url).netloc

        if selector:
            anchors = soup.select(selector)
        else:
            # 啟發式：從語義性容器中找連結
            containers = (
                soup.find_all("article") or
                soup.select("main h2 a, main h3 a, main h4 a") or
                soup.select(".post-title a, .entry-title a, .article-title a, "
                            ".news-title a, .item-title a, h2 > a, h3 > a")
            )
            anchors = containers if selector else []
            # 若啟發式找不到，退而求其次抓全部 <a>
            if not anchors:
                anchors = soup.find_all("a", href=True)

        # 過濾並正規化 URL
        noise_patterns = re.compile(
            r"/(tag|tags|category|categories|author|page|search|login|signup"
            r"|about|contact|privacy|terms)/|#|javascript:|mailto:",
            re.I
        )
        seen, links = set(), []
        for a in anchors:
            href = (a.get("href") if hasattr(a, "get") else
                    a.get("href") if a.name == "a" else
                    a.find("a", href=True) and a.find("a")["href"])
            if not href:
                continue
            full = urljoin(base_url, str(href))
            parsed = urlparse(full)
            # 只接受 http(s)，同域或明確的文章路徑
            if parsed.scheme not in ("http", "https"):
                continue
            if noise_patterns.search(parsed.path):
                continue
            if full not in seen:
                seen.add(full)
                links.append(full)

        return links

    except Exception as e:
        logger.warning(f"連結提取失敗: {e}")
        return []


# ── RSS ───────────────────────────────────────────────────────────────────────

def _feedparser_entry_date(entry) -> Optional[datetime]:
    for attr in ("published_parsed", "updated_parsed", "created_parsed"):
        val = getattr(entry, attr, None)
        if val:
            try:
                ts = time_module.mktime(val)
                return datetime.fromtimestamp(ts, tz=timezone.utc)
            except Exception:
                pass
    for attr in ("published", "updated", "created"):
        val = getattr(entry, attr, None)
        if val:
            dt = _parse_date_str(val)
            if dt:
                return dt
    return None


def fetch_rss(source: dict, cutoff: datetime, max_per_source: int,
              max_chars: int, session: requests.Session) -> list[dict]:
    """抓取 RSS / Atom feed"""
    try:
        resp = session.get(source["url"])
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
    except Exception as e:
        logger.warning(f"[{source['name']}] RSS 抓取失敗: {e}")
        return []

    articles = []
    for entry in feed.entries:
        if len(articles) >= max_per_source:
            break
        pub_dt = _feedparser_entry_date(entry)
        if pub_dt is None or pub_dt < cutoff:
            continue

        raw = ""
        if hasattr(entry, "summary"):
            raw = entry.summary
        elif hasattr(entry, "content") and entry.content:
            raw = entry.content[0].get("value", "")

        articles.append({
            "title":        getattr(entry, "title", "").strip(),
            "url":          getattr(entry, "link", source["url"]).strip(),
            "published_at": pub_dt.isoformat(),
            "summary":      _html_to_text(raw, max_chars) if raw else "",
            "source":       source["name"],
            "category":     source.get("category", "tech"),
        })

    logger.info(f"[{source['name']}] RSS: {len(articles)} 篇")
    return articles


# ── Sitemap ───────────────────────────────────────────────────────────────────

def _resolve_sitemaps(root_url: str, session: requests.Session) -> list[str]:
    """
    從 sitemap index 展開到所有子 sitemap URL。
    若給定 URL 本身不是 index，直接回傳。
    """
    try:
        resp = session.get(root_url)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        tag = root.tag.lower()
        # sitemapindex 格式
        if "sitemapindex" in tag:
            return [
                loc.text.strip()
                for loc in root.iter(f"{{{NS['sm']}}}loc")
                if loc.text
            ]
    except Exception:
        pass
    return [root_url]


def fetch_sitemap(source: dict, cutoff: datetime, max_per_source: int,
                  max_chars: int, session: requests.Session) -> list[dict]:
    """
    解析 XML sitemap（支援一般格式與 Google News sitemap）
    新聞 sitemap 含精確 <news:publication_date>，準確度高。
    """
    sitemap_urls = _resolve_sitemaps(source["url"], session)
    articles = []

    for sm_url in sitemap_urls:
        if len(articles) >= max_per_source:
            break
        try:
            resp = session.get(sm_url)
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
        except Exception as e:
            logger.warning(f"[{source['name']}] Sitemap 解析失敗 {sm_url}: {e}")
            continue

        for url_el in root.iter(f"{{{NS['sm']}}}url"):
            if len(articles) >= max_per_source:
                break

            loc_el = url_el.find(f"{{{NS['sm']}}}loc")
            if loc_el is None or not loc_el.text:
                continue
            article_url = loc_el.text.strip()

            # 嘗試取日期：Google News > lastmod > 略過
            pub_dt = None
            news_date = url_el.find(f"{{{NS['news']}}}publication_date")
            if news_date is not None and news_date.text:
                pub_dt = _parse_date_str(news_date.text)
            if pub_dt is None:
                lastmod = url_el.find(f"{{{NS['sm']}}}lastmod")
                if lastmod is not None and lastmod.text:
                    pub_dt = _parse_date_str(lastmod.text)
            if pub_dt is None or pub_dt < cutoff:
                continue

            # 取標題：Google News sitemap 有 <news:title>
            title = ""
            news_title = url_el.find(f"{{{NS['news']}}}title")
            if news_title is not None and news_title.text:
                title = news_title.text.strip()

            articles.append({
                "title":        title or article_url,
                "url":          article_url,
                "published_at": pub_dt.isoformat(),
                "summary":      "",   # sitemap 通常無內文，讓 Claude 用標題判斷
                "source":       source["name"],
                "category":     source.get("category", "tech"),
            })

    logger.info(f"[{source['name']}] Sitemap: {len(articles)} 篇")
    return articles


# ── Web（通用網頁爬取）────────────────────────────────────────────────────────

def _scrape_article(url: str, max_chars: int,
                    session: requests.Session) -> Optional[dict]:
    """
    抓取單篇文章頁面，使用 trafilatura 提取正文 + metadata。
    trafilatura 實作了 Mozilla Readability 演算法，能處理大多數文章網頁。
    回傳 None 表示無法提取有效內容。
    """
    try:
        resp = session.get(url)
        resp.raise_for_status()
        html = resp.text
    except Exception as e:
        logger.debug(f"  文章抓取失敗 {url}: {e}")
        return None

    # trafilatura 提取（JSON 格式取得 metadata）
    raw_json = trafilatura.extract(
        html,
        url=url,
        output_format="json",
        include_metadata=True,
        include_comments=False,
        no_fallback=False,          # 允許 fallback 策略
        favor_precision=False,      # 允許稍低精準度以提高召回率
    )

    if raw_json:
        try:
            data = json.loads(raw_json)
            title   = data.get("title") or ""
            text    = (data.get("text") or "")[:max_chars]
            date_str = data.get("date") or ""
            pub_dt  = _parse_date_str(date_str) if date_str else None
        except Exception:
            title, text, pub_dt = "", "", None
    else:
        # trafilatura 提取失敗，退回到 BeautifulSoup
        title = ""
        text  = _html_to_text(html, max_chars)
        pub_dt = None

    # 日期再 fallback 到 HTML 解析
    if pub_dt is None:
        pub_dt = _extract_date_from_html(html, url)

    return {
        "title":    title,
        "url":      url,
        "pub_dt":   pub_dt,
        "summary":  text,
    }


def fetch_web(source: dict, cutoff: datetime, max_per_source: int,
              max_chars: int, session: requests.Session) -> list[dict]:
    """
    通用網頁爬取：
    1. 抓取列表頁，提取文章連結
    2. 逐篇抓取，用 trafilatura 提取內容與日期
    3. 依時間過濾（24 小時內）
    """
    selector    = source.get("article_selector")
    max_to_check = source.get("max_articles", max_per_source * 3)

    # Step 1: 抓列表頁
    try:
        resp = session.get(source["url"])
        resp.raise_for_status()
        listing_html = resp.text
    except Exception as e:
        logger.warning(f"[{source['name']}] 列表頁抓取失敗: {e}")
        return []

    links = _extract_article_links(listing_html, source["url"], selector)
    logger.debug(f"[{source['name']}] 找到 {len(links)} 個候選連結")

    # Step 2 & 3: 逐篇抓取並過濾
    articles = []
    checked  = 0

    for link in links:
        if len(articles) >= max_per_source:
            break
        if checked >= max_to_check:
            break
        checked += 1

        result = _scrape_article(link, max_chars, session)
        if result is None:
            continue

        pub_dt = result["pub_dt"]

        # 若完全找不到日期，策略：保留文章讓 Claude 判斷（標記為 unknown）
        if pub_dt is None:
            logger.debug(f"  [日期不明] {result['title'] or link}")
            pub_dt_str = "unknown"
        elif pub_dt < cutoff:
            continue
        else:
            pub_dt_str = pub_dt.isoformat()

        title = result["title"] or link
        articles.append({
            "title":        title.strip(),
            "url":          link,
            "published_at": pub_dt_str,
            "summary":      result["summary"],
            "source":       source["name"],
            "category":     source.get("category", "tech"),
        })

        # 禮貌性延遲，避免對目標網站造成壓力
        time_module.sleep(0.5)

    logger.info(f"[{source['name']}] Web: 檢查 {checked} 篇，收錄 {len(articles)} 篇")
    return articles


# ── 主入口 ────────────────────────────────────────────────────────────────────

def crawl_all(sources_config: dict, settings: dict) -> list[dict]:
    """
    依 sources.json 設定，分派到對應爬取策略，回傳合併文章列表。
    """
    cfg            = settings.get("crawler", {})
    lookback_hours = cfg.get("lookback_hours", 24)
    max_per_source = cfg.get("max_articles_per_source", 5)
    max_chars      = cfg.get("max_content_chars", 500)
    timeout        = cfg.get("request_timeout", 15)
    user_agent     = cfg.get("user_agent", "TechCrawlerBot/1.0")

    cutoff  = datetime.now(tz=timezone.utc) - timedelta(hours=lookback_hours)
    session = _make_session(user_agent, timeout)

    dispatch = {
        "rss":     fetch_rss,
        "sitemap": fetch_sitemap,
        "web":     fetch_web,
    }

    all_articles = []
    for source in sources_config.get("sources", []):
        # 跳過純文件欄位（_section, _doc 開頭的 key-only 物件）
        if "url" not in source or "name" not in source:
            continue

        src_type = source.get("type", "rss")
        fetcher  = dispatch.get(src_type)

        if fetcher is None:
            logger.warning(f"[{source['name']}] 未知 type: {src_type}，跳過")
            continue

        try:
            articles = fetcher(
                source=source,
                cutoff=cutoff,
                max_per_source=max_per_source,
                max_chars=max_chars,
                session=session,
            )
            all_articles.extend(articles)
        except Exception as e:
            logger.error(f"[{source['name']}] 爬取例外: {e}", exc_info=True)

    logger.info(f"共抓取 {len(all_articles)} 篇文章（來自 {len(sources_config.get('sources', []))} 個來源）")
    return all_articles
