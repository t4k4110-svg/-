# ============================================================
# Google画像検索（逆画像検索）モジュール
#
# 優先順位:
#   ① Google Lens (lens.google.com) — AI識別、精度が最も高い
#   ② Google Images カメラ検索 (images.google.com) — 従来型
#
# メルカリ画像はCDNの制限があるため、
# URLをそのまま渡すのではなく「画像をダウンロード→アップロード」方式を使う。
# ============================================================

import re
import os
import logging
import tempfile
from playwright.sync_api import BrowserContext, Page

logger = logging.getLogger(__name__)

# 一時保存する画像ファイルのパス（セッション中に上書き再利用）
_TMP_IMAGE_PATH = os.path.join(tempfile.gettempdir(), "mercari_product_img.jpg")


class GoogleImageSearcher:
    """
    商品画像をGoogleに渡して、商品名・モデル名を特定するクラス。
    MercariMonitor と同じ BrowserContext を使い、専用タブで動作する。
    """

    def __init__(self, context: BrowserContext):
        self._context = context
        self._page: Page | None = None

    def setup(self):
        """専用タブを開く（MercariMonitor.start() の後に呼ぶ）"""
        self._page = self._context.new_page()
        logger.info("Google画像検索タブを起動しました")

    def teardown(self):
        try:
            if self._page:
                self._page.close()
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    #  公開メソッド
    # ------------------------------------------------------------------ #

    def identify(self, image_url: str) -> str:
        """
        商品画像URLからGoogle画像検索で商品名を特定する。

        処理の流れ:
          1. Playwrightで画像を直接ダウンロード（認証・リファラ問題を回避）
          2. Google Lensにファイルとしてアップロードして検索
          3. 失敗したら Google Images のカメラ検索（URL渡し）で再試行
          4. それも失敗したら空文字を返す（呼び出し元がタイトルで代替）

        Returns: 特定された商品名（例: "Patagonia Fleece Jacket"）or ""
        """
        if not image_url or not self._page:
            return ""

        # ── Step 1: 画像をダウンロード ──
        image_path = self._download_image(image_url)

        # ── Step 2: Google Lens にアップロードして検索 ──
        if image_path:
            result = self._search_google_lens(image_path)
            if result:
                return result

        # ── Step 3: URL渡しで Google Images カメラ検索 ──
        result = self._search_google_images_url(image_url)
        if result:
            return result

        logger.warning("Google画像検索: 商品を特定できませんでした")
        return ""

    # ------------------------------------------------------------------ #
    #  Step 1 — 画像ダウンロード
    # ------------------------------------------------------------------ #

    def _download_image(self, image_url: str) -> str | None:
        """
        Playwright の fetch API を使って画像を取得し、一時ファイルに保存する。
        通常の requests と異なり、ブラウザのCookie・Refererが自動で付与されるため
        メルカリCDNのアクセス制限を回避できる。
        Returns: 保存したファイルのパス、失敗時は None
        """
        try:
            response = self._page.request.get(
                image_url,
                headers={"Referer": "https://jp.mercari.com/"},
                timeout=10_000,
            )
            if response.ok:
                with open(_TMP_IMAGE_PATH, "wb") as f:
                    f.write(response.body())
                logger.debug(f"画像ダウンロード完了: {_TMP_IMAGE_PATH}")
                return _TMP_IMAGE_PATH
        except Exception as e:
            logger.debug(f"画像ダウンロード失敗: {e}")
        return None

    # ------------------------------------------------------------------ #
    #  Step 2 — Google Lens（ファイルアップロード）
    # ------------------------------------------------------------------ #

    def _search_google_lens(self, image_path: str) -> str:
        """
        Google Lens (lens.google.com) に画像をアップロードして商品を特定する。
        Google Lens は AI を使った商品識別に最も優れている。
        """
        try:
            self._page.goto(
                "https://lens.google.com/",
                timeout=15_000,
                wait_until="domcontentloaded",
            )
            self._page.wait_for_timeout(1500)

            # ファイルアップロードの input 要素を探す
            # Google Lens は通常 <input type="file"> を持つ
            file_input = self._page.query_selector('input[type="file"]')
            if not file_input:
                # 非表示の場合は JavaScript で表示させる
                self._page.evaluate(
                    """() => {
                        const inputs = document.querySelectorAll('input[type="file"]');
                        inputs.forEach(el => el.style.display = 'block');
                    }"""
                )
                file_input = self._page.query_selector('input[type="file"]')

            if not file_input:
                logger.debug("Google Lens: ファイル入力欄が見つかりません")
                return ""

            # 画像ファイルをセット
            file_input.set_input_files(image_path)
            self._page.wait_for_timeout(4000)

            return self._extract_product_name(self._page)

        except Exception as e:
            logger.debug(f"Google Lens 検索エラー: {e}")
            return ""

    # ------------------------------------------------------------------ #
    #  Step 3 — Google Images カメラ検索（URLを貼り付ける方式）
    # ------------------------------------------------------------------ #

    def _search_google_images_url(self, image_url: str) -> str:
        """
        Google Images のカメラ検索UIを操作して逆画像検索を行う。
        Google Lens が失敗した場合の代替手段。
        """
        try:
            self._page.goto(
                "https://images.google.com/",
                timeout=15_000,
                wait_until="domcontentloaded",
            )
            self._page.wait_for_timeout(1200)

            # カメラアイコン（画像で検索ボタン）を探してクリック
            camera = (
                self._page.query_selector('[aria-label="画像で検索"]')
                or self._page.query_selector('[title="画像で検索"]')
                or self._page.query_selector('div[jsname="R5mgy"]')
                or self._page.query_selector('[data-ved] div[role="button"]')
            )
            if not camera:
                logger.debug("Google Images: カメラボタンが見つかりません")
                return ""

            camera.click()
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

            # URL入力欄に画像URLを入力して検索
            url_input = (
                self._page.query_selector('input[placeholder*="URL"]')
                or self._page.query_selector('input[type="url"]')
            )
            if not url_input:
                logger.debug("Google Images: URL入力欄が見つかりません")
                return ""

            url_input.fill(image_url)
            self._page.wait_for_timeout(300)
            url_input.press("Enter")
            self._page.wait_for_timeout(4000)

            return self._extract_product_name(self._page)

        except Exception as e:
            logger.debug(f"Google Images URL検索エラー: {e}")
            return ""

    # ------------------------------------------------------------------ #
    #  結果ページから商品名を抽出
    # ------------------------------------------------------------------ #

    def _extract_product_name(self, page: Page) -> str:
        """
        Google Lens / Google Images の検索結果ページから商品名テキストを取り出す。
        複数のパターンを試して最初に見つかったものを返す。
        """
        body_text = page.inner_text("body")

        # ── パターン1: Google Lensの「このアイテムについて」──
        m = re.search(r"このアイテムについて\s*\n\s*(.+?)(?:\n|$)", body_text)
        if m:
            return _clean(m.group(1))

        # ── パターン2: 日本語「推定クエリ」──
        m = re.search(
            r"推定(?:される)?(?:クエリ|検索)[^：:]*[：:]\s*(.+?)(?:\n|$)", body_text
        )
        if m:
            return _clean(m.group(1))

        # ── パターン3: 英語 "Best guess for this image" ──
        m = re.search(
            r"Best guess[^:]*:\s*(.+?)(?:\n|$)", body_text, re.IGNORECASE
        )
        if m:
            return _clean(m.group(1))

        # ── パターン4: 「〜を検索」という Lens のリンクテキスト ──
        m = re.search(r"「(.+?)」を検索", body_text)
        if m:
            return _clean(m.group(1))

        # ── パターン5: h3 タグの最初の有意なテキスト ──
        for el in page.query_selector_all("h3"):
            try:
                text = el.inner_text().strip()
                if len(text) > 8 and "Google" not in text and "検索" not in text:
                    return _clean(text)
            except Exception:
                continue

        return ""


# ------------------------------------------------------------------ #
#  ユーティリティ
# ------------------------------------------------------------------ #

def _clean(text: str) -> str:
    """テキストの余分なスペース・改行を除去して最大80文字に絞る"""
    return re.sub(r"\s+", " ", text).strip()[:80]
