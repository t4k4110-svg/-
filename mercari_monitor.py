# ============================================================
# メルカリ監視モジュール
# Playwrightを使ってメルカリを自動でブラウジングし、
# 新着商品リストと相場価格を取得する
# ============================================================

import re
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
        self.seen_ids: set[str] = set()
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None          # メイン検索ページ
        self._market_page = None   # 相場調査専用ページ（並行処理用）

    # ------------------------------------------------------------------ #
    #  ライフサイクル
    # ------------------------------------------------------------------ #

    def start(self):
        """ブラウザを起動してメルカリのトップページを開く"""
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=False,
            slow_mo=50,
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
        # ページを2つ用意：メイン検索用 + 相場調査用
        self._page = self._context.new_page()
        self._market_page = self._context.new_page()

        self._safe_goto(self._page, "https://jp.mercari.com/", wait=3000)
        logger.info("ブラウザ起動完了（ページ2枚）")

    def stop(self):
        """ブラウザを閉じる"""
        try:
            if self._browser:
                self._browser.close()
            if self._pw:
                self._pw.stop()
        except Exception:
            pass

    @property
    def context(self):
        """image_searchモジュールにContextを渡すためのプロパティ"""
        return self._context

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
        self._safe_goto(self._page, url, wait=4000)

        all_listings = self._extract_listings(self._page)
        logger.info(f"[{keyword}] 取得件数: {len(all_listings)}")

        new_listings = []
        for item in all_listings:
            if item["id"] and item["id"] not in self.seen_ids:
                new_listings.append(item)
                self.seen_ids.add(item["id"])

        return new_listings

    def _extract_listings(self, page) -> list[dict]:
        """現在のページから商品情報を取り出す"""
        listings = []
        selectors = [
            '[data-testid="item-cell"]',
            'li[data-component-name]',
            '.items-box',
            '[class*="itemCell"]',
            'ul[class*="item"] > li',
        ]

        items = []
        for sel in selectors:
            found = page.query_selector_all(sel)
            if found:
                items = found
                break

        if not items:
            logger.warning("商品リストのセレクタが見つかりませんでした")
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
        """1つの商品要素からID・タイトル・価格・URL・画像URLを抽出"""
        # URL / ID
        link = item.query_selector("a[href]")
        href = link.get_attribute("href") if link else ""
        m = re.search(r"m(\d+)", href or "")
        item_id = m.group(0) if m else ""

        # 価格
        raw_text = item.inner_text()
        price = _extract_price(raw_text)
        if price is None or not (MIN_PRICE <= price <= MAX_PRICE):
            return None

        # タイトル（最長テキストを採用）
        title = _longest_text(item)

        # 画像URL（逆画像検索の入力に使う）
        img = item.query_selector("img")
        img_url = img.get_attribute("src") if img else ""
        # サムネイルURLを元のサイズに変換（メルカリのURL規則）
        img_url = _normalize_mercari_image_url(img_url)

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
    # ※ image_searchで特定した商品名で検索することで精度向上
    # ------------------------------------------------------------------ #

    def get_market_price(self, product_name: str, keyword: str) -> tuple[int | None, int]:
        """
        商品名でメルカリの「売り切れ」商品を検索し、価格の平均値を相場とする。
        product_name: 画像検索で特定した商品名（なければタイトル）
        Returns: (相場価格 or None, サンプル数)
        """
        search_term = _build_search_query(keyword, product_name)
        url = (
            f"{MERCARI_SEARCH_URL}"
            f"?keyword={search_term}"
            f"&status=sold_out"
            f"&sort=created_time"
            f"&order=desc"
        )
        # 相場調査は専用ページで行う（メインページの状態を壊さない）
        self._safe_goto(self._market_page, url, wait=4000)

        prices = []
        items = (
            self._market_page.query_selector_all('[data-testid="item-cell"]')
            or self._market_page.query_selector_all('li[data-component-name]')
        )

        for item in (items or [])[:30]:
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

    def _safe_goto(self, page, url: str, wait: int = 3000):
        """ページ遷移（タイムアウト・ネットワークエラーに対応）"""
        try:
            page.goto(url, timeout=30_000, wait_until="domcontentloaded")
            page.wait_for_timeout(wait)
        except PlaywrightTimeout:
            logger.warning(f"タイムアウト: {url}")
        except Exception as e:
            logger.warning(f"ページ遷移エラー: {e}")


# ------------------------------------------------------------------ #
#  モジュールレベルのユーティリティ関数
# ------------------------------------------------------------------ #

def _extract_price(text: str) -> int | None:
    m = re.search(r"¥\s*([\d,]+)", text or "")
    if not m:
        m = re.search(r"([\d,]{3,})\s*円", text or "")
    if m:
        return int(m.group(1).replace(",", ""))
    return None


def _longest_text(element) -> str:
    candidates = []
    for el in element.query_selector_all("p, span, div, h3"):
        try:
            t = el.inner_text().strip()
            if t and not re.search(r"^¥|^\d+円$", t) and len(t) > 3:
                candidates.append(t)
        except Exception:
            continue
    if not candidates:
        return element.inner_text().split("\n")[0].strip()
    return max(candidates, key=len)[:80]


def _normalize_mercari_image_url(url: str) -> str:
    """
    メルカリのサムネイルURLを、より大きな画像のURLに変換する。
    例: ...c!small.jpg → ...c!large.jpg
    """
    if not url:
        return url
    # サムネイル指定を大画面サイズに変更
    url = re.sub(r"c!small", "c!large", url)
    url = re.sub(r"w=\d+", "w=800", url)
    url = re.sub(r"h=\d+", "h=800", url)
    return url


def _build_search_query(keyword: str, product_name: str) -> str:
    noise = [
        "新品", "未使用", "美品", "未着用", "タグ付き",
        "送料込", "送料無料", "即購入OK", "値下げ", "訳あり",
        "【", "】", "（", "）", "(", ")", "！", "!",
    ]
    clean = product_name
    for w in noise:
        clean = clean.replace(w, " ")
    clean = re.sub(r"\s+", " ", clean).strip()[:30]
    return f"{keyword} {clean}".strip()


def _trimmed_mean(prices: list[int]) -> int:
    prices_sorted = sorted(prices)
    n = len(prices_sorted)
    trim = max(1, n // 10)
    trimmed = prices_sorted[trim: n - trim] if n - trim > trim else prices_sorted
    return int(sum(trimmed) / len(trimmed))
