# ============================================================
# Google検索で相場価格を補足調査するモジュール
# MercariMonitorが「データ不足」を返した場合の代替手段
# Playwrightを使ってGoogle検索結果からメルカリの価格を集める
# ============================================================

import re
import logging
import time
from playwright.sync_api import Page

logger = logging.getLogger(__name__)


def google_search_market_price(page: Page, keyword: str, product_title: str) -> tuple[int | None, int]:
    """
    Google検索「{キーワード} {商品名} メルカリ 相場」で価格を調査する。
    Returns: (中央値価格 or None, 取得した価格の件数)
    """
    query = _build_google_query(keyword, product_title)
    logger.info(f"Google検索: {query}")

    try:
        page.goto(
            f"https://www.google.co.jp/search?q={query}&hl=ja",
            timeout=20_000,
            wait_until="domcontentloaded",
        )
        page.wait_for_timeout(2500)

        # CAPTCHA検出
        if _is_captcha_page(page):
            logger.warning("Google CAPTCHAが表示されました。しばらく待ちます...")
            time.sleep(30)
            return None, 0

        prices = _extract_prices_from_google(page)
        logger.info(f"Google検索で価格を{len(prices)}件取得")

        if not prices:
            return None, 0

        median = _median(prices)
        return median, len(prices)

    except Exception as e:
        logger.warning(f"Google検索エラー: {e}")
        return None, 0


def _build_google_query(keyword: str, product_title: str) -> str:
    """Googleに送る検索文字列を組み立てる"""
    # ノイズ除去
    noise = ["新品", "未使用", "美品", "未着用", "送料込", "送料無料",
             "即購入OK", "値下げ", "【", "】", "（", "）"]
    clean = product_title
    for w in noise:
        clean = clean.replace(w, " ")
    clean = re.sub(r"\s+", " ", clean).strip()[:25]

    # 例: "パタゴニア フリース メルカリ 相場"
    return f"{keyword} {clean} メルカリ 相場"


def _extract_prices_from_google(page: Page) -> list[int]:
    """Google検索結果のHTML全文から ¥xxxx 形式の価格をすべて抽出"""
    body_text = page.inner_text("body")
    raw_matches = re.findall(r"¥\s*([\d,]+)", body_text)

    prices = []
    for raw in raw_matches:
        try:
            p = int(raw.replace(",", ""))
            # メルカリの衣類価格として妥当な範囲のみ採用
            if 500 <= p <= 500_000:
                prices.append(p)
        except ValueError:
            continue

    return prices


def _is_captcha_page(page: Page) -> bool:
    """Googleのロボット確認ページかどうかを判定"""
    url = page.url
    title = page.title()
    return "sorry" in url or "captcha" in url.lower() or "unusual traffic" in title.lower()


def _median(values: list[int]) -> int:
    """中央値を返す"""
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    if n % 2 == 1:
        return sorted_vals[n // 2]
    return (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) // 2
