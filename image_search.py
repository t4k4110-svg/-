# ============================================================
# Google逆画像検索モジュール
# メルカリの商品画像URLをGoogleに渡し、商品名・モデルを特定する
# タイトルが曖昧・未記載の商品に対応するための核心モジュール
# ============================================================

import re
import logging
import time
from playwright.sync_api import BrowserContext, Page

logger = logging.getLogger(__name__)


class GoogleImageSearcher:
    """
    Google画像検索（逆画像検索）を使って商品名を特定するクラス。
    同じブラウザContext内に専用ページを作成して検索を行う。
    """

    def __init__(self, context: BrowserContext):
        self._context = context
        self._page: Page | None = None

    def setup(self):
        """専用ページを起動する（MercariMonitor.start()後に呼ぶ）"""
        self._page = self._context.new_page()

    def teardown(self):
        """ページを閉じる"""
        try:
            if self._page:
                self._page.close()
        except Exception:
            pass

    def identify(self, image_url: str) -> str:
        """
        商品画像URLからGoogleで商品名を特定する。
        Returns: 特定された商品名（例: "Patagonia Retro-X Fleece"）
                 特定できなかった場合は空文字列 ""
        """
        if not image_url or not self._page:
            return ""

        try:
            # ── 方法①: Google Imagesのカメラ検索UIを操作 ──
            result = self._search_via_google_images_ui(image_url)
            if result:
                logger.info(f"画像検索結果: {result}")
                return result

            # ── 方法②: searchbyimage URLに直接アクセス ──
            result = self._search_via_url_param(image_url)
            if result:
                logger.info(f"画像検索結果(fallback): {result}")
                return result

            logger.warning("画像から商品名を特定できませんでした")
            return ""

        except Exception as e:
            logger.warning(f"画像検索エラー: {e}")
            return ""

    # ------------------------------------------------------------------ #
    #  方法① Google Imagesのカメラ検索UIをPlaywrightで操作
    # ------------------------------------------------------------------ #

    def _search_via_google_images_ui(self, image_url: str) -> str:
        try:
            self._page.goto(
                "https://images.google.com/",
                timeout=15_000,
                wait_until="domcontentloaded",
            )
            self._page.wait_for_timeout(1500)

            # カメラアイコンを探してクリック
            camera_btn = (
                self._page.query_selector('[aria-label="画像で検索"]')
                or self._page.query_selector('[title="画像で検索"]')
                or self._page.query_selector('div[jsname="R5mgy"]')
            )
            if not camera_btn:
                return ""
            camera_btn.click()
            self._page.wait_for_timeout(800)

            # 「URLを貼り付け」タブを探してクリック
            url_tab = (
                self._page.query_selector('text=URLを貼り付け')
                or self._page.query_selector('[aria-label*="URL"]')
                or self._page.query_selector('a:has-text("URL")')
            )
            if url_tab:
                url_tab.click()
                self._page.wait_for_timeout(500)

            # URL入力フィールドにメルカリ画像URLを入力
            url_input = (
                self._page.query_selector('input[placeholder*="URL"]')
                or self._page.query_selector('input[type="url"]')
                or self._page.query_selector('input[jsname]')
            )
            if not url_input:
                return ""

            url_input.fill(image_url)
            self._page.wait_for_timeout(300)
            url_input.press("Enter")
            self._page.wait_for_timeout(4000)

            return self._extract_product_name(self._page)

        except Exception as e:
            logger.debug(f"UI検索エラー: {e}")
            return ""

    # ------------------------------------------------------------------ #
    #  方法② searchbyimage エンドポイントに画像URLを渡す
    # ------------------------------------------------------------------ #

    def _search_via_url_param(self, image_url: str) -> str:
        try:
            encoded_url = image_url.replace("&", "%26")
            search_url = (
                f"https://www.google.com/searchbyimage"
                f"?sbisrc=tg&image_url={encoded_url}&safe=off&hl=ja"
            )
            self._page.goto(search_url, timeout=20_000, wait_until="domcontentloaded")
            self._page.wait_for_timeout(3000)
            return self._extract_product_name(self._page)

        except Exception as e:
            logger.debug(f"URLパラメータ検索エラー: {e}")
            return ""

    # ------------------------------------------------------------------ #
    #  検索結果ページからの商品名抽出
    # ------------------------------------------------------------------ #

    def _extract_product_name(self, page: Page) -> str:
        """Google画像検索の結果ページから商品名を取り出す"""
        body_text = page.inner_text("body")

        # ── パターン1: 日本語「推定クエリ」──
        m = re.search(
            r"推定(?:される)?(?:クエリ|検索)[^：:]*[：:]\s*(.+?)(?:\n|$)", body_text
        )
        if m:
            return _clean(m.group(1))

        # ── パターン2: 英語 "Best guess for this image" ──
        m = re.search(
            r"Best guess[^:]*:\s*(.+?)(?:\n|$)", body_text, re.IGNORECASE
        )
        if m:
            return _clean(m.group(1))

        # ── パターン3: Lensの「このアイテムについて」見出し ──
        m = re.search(
            r"このアイテムについて\s*\n\s*(.+?)(?:\n|$)", body_text
        )
        if m:
            return _clean(m.group(1))

        # ── パターン4: h3の最初のテキスト（検索結果タイトル）──
        for el in page.query_selector_all("h3"):
            try:
                text = el.inner_text().strip()
                # 短すぎるもの、Google UI文言を除外
                if len(text) > 8 and "Google" not in text:
                    return _clean(text)
            except Exception:
                continue

        return ""


# ------------------------------------------------------------------ #
#  ユーティリティ
# ------------------------------------------------------------------ #

def _clean(text: str) -> str:
    """抽出テキストの前後の空白・改行を除去して返す"""
    return re.sub(r"\s+", " ", text).strip()[:80]
