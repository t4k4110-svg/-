# ============================================================
# メルカリ監視モジュール
# Playwrightを使ってメルカリを自動でブラウジングし、
# 新着商品リストと相場価格を取得する
# ============================================================

import re
import time
import logging
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from config import (
    MERCARI_SEARCH_URL, MIN_PRICE, MAX_PRICE,
    MIN_MARKET_PRICE_SAMPLES
)

logger = logging.getLogger(__name__)


class MercariMonitor:
    """メルカリの商品監視クラス"""

    def __init__(self):
        self.seen_ids: set[str] = set()   # 一度見た商品IDを記録（重複通知防止）
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None

    # ------------------------------------------------------------------ #
    #  ライフサイクル
    # ------------------------------------------------------------------ #

    def start(self):
        """ブラウザを起動してメルカリのトップページを開く"""
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=False,          # ブラウザを画面に表示する
            slow_mo=50,              # 操作の間隔を少し開けて人間らしく見せる
            args=["--lang=ja-JP"],
        )
        self._context = self._browser.new_context(
            locale="ja-JP",
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        self._page = self._context.new_page()
        # 最初にトップページへアクセスしてCookieを受け入れる
        self._safe_goto("https://jp.mercari.com/", wait=3000)
        logger.info("ブラウザ起動完了")

    def stop(self):
        """ブラウザを閉じる"""
        try:
            if self._browser:
                self._browser.close()
            if self._pw:
                self._pw.stop()
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    #  新着商品取得
    # ------------------------------------------------------------------ #

    def get_new_listings(self, keyword: str) -> list[dict]:
        """
        指定キーワードでメルカリを検索し、「まだ見ていない」新着商品だけを返す
        """
        url = (
            f"{MERCARI_SEARCH_URL}"
            f"?keyword={keyword}"
            f"&status=on_sale"
            f"&sort=created_time"
            f"&order=desc"
        )
        self._safe_goto(url, wait=4000)

        all_listings = self._extract_listings()
        logger.info(f"[{keyword}] 取得件数: {len(all_listings)}")

        new_listings = []
        for item in all_listings:
            if item["id"] and item["id"] not in self.seen_ids:
                new_listings.append(item)
                self.seen_ids.add(item["id"])

        return new_listings

    def _extract_listings(self) -> list[dict]:
        """現在のページから商品情報を取り出す"""
        listings = []

        # メルカリのセレクタは変わることがあるため複数試す
        selectors = [
            '[data-testid="item-cell"]',
            'li[data-component-name]',
            '.items-box',
            '[class*="itemCell"]',
            'ul[class*="item"] > li',
        ]

        items = []
        for sel in selectors:
            found = self._page.query_selector_all(sel)
            if found:
                items = found
                break

        if not items:
            logger.warning("商品リストのセレクタが見つかりませんでした。ページ構造が変わった可能性があります。")
            return listings

        for item in items:
            try:
                data = self._parse_item_element(item)
                if data:
                    listings.append(data)
            except Exception as e:
                logger.debug(f"アイテム解析スキップ: {e}")

        return listings

    def _parse_item_element(self, item) -> dict | None:
        """1つの商品要素からID・タイトル・価格・URLを抽出"""
        # --- URL / ID ---
        link = item.query_selector("a[href]")
        href = link.get_attribute("href") if link else ""
        m = re.search(r"m(\d+)", href or "")
        item_id = m.group(0) if m else ""

        # --- 価格（¥記号付きの数字を正規表現で取得）---
        raw_text = item.inner_text()
        price = _extract_price(raw_text)
        if price is None or not (MIN_PRICE <= price <= MAX_PRICE):
            return None

        # --- タイトル（最長のテキストノードを採用）---
        title = _longest_text(item)

        # --- 画像URL ---
        img = item.query_selector("img")
        img_url = img.get_attribute("src") if img else ""

        full_url = href if href.startswith("http") else f"https://jp.mercari.com{href}"

        return {
            "id": item_id,
            "title": title,
            "price": price,
            "url": full_url,
            "image_url": img_url,
        }

    # ------------------------------------------------------------------ #
    #  相場価格取得（売り切れ商品の中央値を使用）
    # ------------------------------------------------------------------ #

    def get_market_price(self, product_title: str, keyword: str) -> tuple[int | None, int]:
        """
        「売り切れ」商品を検索し、その価格の中央値を相場とする。
        Returns: (相場価格 or None, サンプル数)
        """
        search_term = _build_search_query(keyword, product_title)
        url = (
            f"{MERCARI_SEARCH_URL}"
            f"?keyword={search_term}"
            f"&status=sold_out"
            f"&sort=created_time"
            f"&order=desc"
        )
        self._safe_goto(url, wait=4000)

        prices = []
        items_selector = self._page.query_selector_all('[data-testid="item-cell"]') or \
                         self._page.query_selector_all('li[data-component-name]')

        for item in (items_selector or [])[:30]:
            try:
                price = _extract_price(item.inner_text())
                if price and MIN_PRICE <= price <= MAX_PRICE:
                    prices.append(price)
            except Exception:
                continue

        if len(prices) < MIN_MARKET_PRICE_SAMPLES:
            return None, len(prices)

        market_price = _trimmed_mean(prices)
        return market_price, len(prices)

    # ------------------------------------------------------------------ #
    #  内部ユーティリティ
    # ------------------------------------------------------------------ #

    def _safe_goto(self, url: str, wait: int = 3000):
        """ページ遷移（タイムアウト・ネットワークエラーに対応）"""
        try:
            self._page.goto(url, timeout=30_000, wait_until="domcontentloaded")
            self._page.wait_for_timeout(wait)
        except PlaywrightTimeout:
            logger.warning(f"タイムアウト: {url}")
        except Exception as e:
            logger.warning(f"ページ遷移エラー: {e}")


# ------------------------------------------------------------------ #
#  モジュールレベルのユーティリティ関数
# ------------------------------------------------------------------ #

def _extract_price(text: str) -> int | None:
    """テキストから ¥1,234 形式の価格を抽出して整数で返す"""
    m = re.search(r"¥\s*([\d,]+)", text or "")
    if not m:
        # 円表記のみのケース
        m = re.search(r"([\d,]{3,})\s*円", text or "")
    if m:
        return int(m.group(1).replace(",", ""))
    return None


def _longest_text(element) -> str:
    """要素内のテキストノードのうち最長のものをタイトルと判断"""
    candidates = []
    for el in element.query_selector_all("p, span, div, h3"):
        try:
            t = el.inner_text().strip()
            # 価格行や短すぎる行を除く
            if t and not re.search(r"^¥|^\d+円$", t) and len(t) > 3:
                candidates.append(t)
        except Exception:
            continue
    if not candidates:
        return element.inner_text().split("\n")[0].strip()
    return max(candidates, key=len)[:80]


def _build_search_query(keyword: str, title: str) -> str:
    """不要な単語を除いた検索クエリを組み立てる"""
    noise = [
        "新品", "未使用", "美品", "未着用", "タグ付き",
        "送料込", "送料無料", "即購入OK", "値下げ", "訳あり",
        "【", "】", "（", "）", "(", ")", "！", "!",
    ]
    clean = title
    for w in noise:
        clean = clean.replace(w, " ")
    # 連続スペースを1つに圧縮し、先頭30文字を使用
    clean = re.sub(r"\s+", " ", clean).strip()[:30]
    return f"{keyword} {clean}".strip()


def _trimmed_mean(prices: list[int]) -> int:
    """外れ値を除いた平均値（上下10%を切り捨て）"""
    prices_sorted = sorted(prices)
    n = len(prices_sorted)
    trim = max(1, n // 10)
    trimmed = prices_sorted[trim: n - trim] if n - trim > trim else prices_sorted
    return int(sum(trimmed) / len(trimmed))
