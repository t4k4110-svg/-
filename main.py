# ============================================================
# メルカリ割安商品検出ツール — メインウィンドウ
# ============================================================

import tkinter as tk
from tkinter import ttk, scrolledtext
import threading
import time
import logging
import webbrowser
import sys

from mercari_monitor import MercariMonitor
from price_research import google_search_market_price
from notifier import notify_bargain
from config import (
    KEYWORDS, PRICE_RATIO_THRESHOLD,
    CHECK_INTERVAL_SECONDS, MIN_MARKET_PRICE_SAMPLES
)

# ログ設定（コンソールにも出力）
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


class BargainDetectorApp:
    """アプリケーションのメインクラス"""

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("メルカリ割安商品検出ツール")
        self.root.geometry("900x680")
        self.root.resizable(True, True)
        self.root.configure(bg="#F5F6FA")

        self._monitor: MercariMonitor | None = None
        self._running = False
        self._thread: threading.Thread | None = None

        self._setup_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------ #
    #  UIセットアップ
    # ------------------------------------------------------------------ #

    def _setup_ui(self):
        # ── タイトルバー ──
        top = tk.Frame(self.root, bg="#2C3E50", padx=16, pady=10)
        top.pack(fill=tk.X)
        tk.Label(
            top, text="メルカリ 割安商品検出ツール",
            font=("Meiryo UI", 15, "bold"),
            bg="#2C3E50", fg="white",
        ).pack(side=tk.LEFT)

        # ── コントロールパネル ──
        ctrl = tk.Frame(self.root, bg="#ECF0F1", padx=12, pady=8)
        ctrl.pack(fill=tk.X)

        self._start_btn = tk.Button(
            ctrl, text="▶ 監視開始",
            command=self._start,
            font=("Meiryo UI", 11, "bold"),
            bg="#27AE60", fg="white",
            relief=tk.FLAT, padx=18, pady=6, cursor="hand2",
        )
        self._start_btn.pack(side=tk.LEFT, padx=4)

        self._stop_btn = tk.Button(
            ctrl, text="⏹ 停止",
            command=self._stop,
            font=("Meiryo UI", 11),
            bg="#E74C3C", fg="white",
            relief=tk.FLAT, padx=18, pady=6, cursor="hand2",
            state=tk.DISABLED,
        )
        self._stop_btn.pack(side=tk.LEFT, padx=4)

        self._status_var = tk.StringVar(value="待機中")
        tk.Label(
            ctrl, textvariable=self._status_var,
            font=("Meiryo UI", 10), bg="#ECF0F1", fg="#7F8C8D",
        ).pack(side=tk.LEFT, padx=16)

        # ── キーワード表示 ──
        kw_frame = tk.Frame(self.root, bg="#F5F6FA", padx=12, pady=4)
        kw_frame.pack(fill=tk.X)
        kw_label = "  監視キーワード: " + " / ".join(KEYWORDS)
        kw_label += f"    閾値: 相場の {PRICE_RATIO_THRESHOLD:.0%} 以下"
        tk.Label(
            kw_frame, text=kw_label,
            font=("Meiryo UI", 9), bg="#F5F6FA", fg="#555",
        ).pack(anchor=tk.W)

        # ── 割安商品リスト ──
        list_frame = tk.LabelFrame(
            self.root, text=" 💰 発見した割安商品 ",
            font=("Meiryo UI", 10, "bold"),
            bg="#F5F6FA", fg="#2C3E50", padx=6, pady=4,
        )
        list_frame.pack(fill=tk.X, padx=10, pady=(6, 0))

        cols = ("商品名", "出品価格", "相場価格", "相場比", "URL")
        self._tree = ttk.Treeview(list_frame, columns=cols, show="headings", height=7)
        widths = [320, 90, 90, 70, 260]
        for col, w in zip(cols, widths):
            self._tree.heading(col, text=col)
            self._tree.column(col, width=w, anchor=tk.CENTER if w < 200 else tk.W)
        self._tree.pack(fill=tk.X, expand=False)
        self._tree.bind("<Double-1>", self._open_selected_url)

        tk.Label(
            list_frame, text="→ 行をダブルクリックすると商品ページを開きます",
            font=("Meiryo UI", 8), bg="#F5F6FA", fg="#999",
        ).pack(anchor=tk.W)

        # ── ログ ──
        log_frame = tk.LabelFrame(
            self.root, text=" ログ ",
            font=("Meiryo UI", 10, "bold"),
            bg="#F5F6FA", fg="#2C3E50", padx=6, pady=4,
        )
        log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=6)

        self._log_box = scrolledtext.ScrolledText(
            log_frame, font=("Consolas", 9),
            bg="#1E272E", fg="#DFE6E9",
            insertbackground="white",
            wrap=tk.WORD,
        )
        self._log_box.pack(fill=tk.BOTH, expand=True)

        # ログ色タグ
        self._log_box.tag_configure("info",    foreground="#DFE6E9")
        self._log_box.tag_configure("warn",    foreground="#F39C12")
        self._log_box.tag_configure("bargain", foreground="#2ECC71", font=("Consolas", 9, "bold"))
        self._log_box.tag_configure("error",   foreground="#E74C3C")

    # ------------------------------------------------------------------ #
    #  コントロール
    # ------------------------------------------------------------------ #

    def _start(self):
        self._running = True
        self._start_btn.config(state=tk.DISABLED)
        self._stop_btn.config(state=tk.NORMAL)
        self._log("監視を開始します…", "info")
        self._thread = threading.Thread(target=self._monitoring_loop, daemon=True)
        self._thread.start()

    def _stop(self):
        self._running = False
        self._start_btn.config(state=tk.NORMAL)
        self._stop_btn.config(state=tk.DISABLED)
        self._set_status("停止中…")
        self._log("監視停止リクエストを受け付けました", "warn")

    def _on_close(self):
        self._running = False
        if self._monitor:
            self._monitor.stop()
        self.root.destroy()

    # ------------------------------------------------------------------ #
    #  監視ループ（バックグラウンドスレッド）
    # ------------------------------------------------------------------ #

    def _monitoring_loop(self):
        try:
            self._monitor = MercariMonitor()
            self._monitor.start()
            self._log("ブラウザを起動しました", "info")

            while self._running:
                for keyword in KEYWORDS:
                    if not self._running:
                        break
                    self._set_status(f"「{keyword}」を検索中…")
                    self._log(f"--- 「{keyword}」を検索 ---", "info")

                    new_items = self._monitor.get_new_listings(keyword)
                    self._log(f"新着: {len(new_items)} 件", "info")

                    for item in new_items:
                        if not self._running:
                            break
                        self._analyze(item, keyword)

                if not self._running:
                    break

                # 次のチェックまでカウントダウン
                for remaining in range(CHECK_INTERVAL_SECONDS, 0, -1):
                    if not self._running:
                        break
                    self._set_status(f"次のチェックまで {remaining} 秒…")
                    time.sleep(1)

        except Exception as e:
            self._log(f"致命的エラー: {e}", "error")
            logger.exception("監視ループでエラーが発生しました")
        finally:
            if self._monitor:
                self._monitor.stop()
            self._log("監視を終了しました", "warn")
            self.root.after(0, self._reset_buttons)

    def _analyze(self, item: dict, keyword: str):
        """1件の商品を分析して割安か判定する"""
        title = item["title"] or "(タイトル不明)"
        price = item["price"]
        self._log(f"  分析: {title[:40]}  ¥{price:,}", "info")

        # ① メルカリの売り切れ実績から相場を取得
        market_price, samples = self._monitor.get_market_price(title, keyword)

        # ② メルカリデータが不足ならGoogle検索で補足
        if (market_price is None or samples < MIN_MARKET_PRICE_SAMPLES):
            self._log(f"  メルカリデータ不足（{samples}件）→ Google検索で補足", "warn")
            market_price, samples = google_search_market_price(
                self._monitor._page, keyword, title
            )

        if market_price is None:
            self._log(f"  相場データなし（スキップ）", "warn")
            return

        ratio = price / market_price
        self._log(
            f"  相場: ¥{market_price:,}（{samples}件） / 比率: {ratio:.0%}",
            "info",
        )

        if ratio <= PRICE_RATIO_THRESHOLD:
            self._log(
                f"  ★ 割安発見! ¥{price:,}（相場比 {ratio:.0%}） → {item['url']}",
                "bargain",
            )
            # GUIを安全に更新（メインスレッドへ委譲）
            self.root.after(
                0, self._add_bargain_row, item, market_price, ratio
            )
            notify_bargain(title, price, market_price, ratio, item["url"])

    # ------------------------------------------------------------------ #
    #  GUIヘルパー（メインスレッドから呼ぶ）
    # ------------------------------------------------------------------ #

    def _add_bargain_row(self, item: dict, market_price: int, ratio: float):
        self._tree.insert(
            "", 0,
            values=(
                item["title"][:50],
                f"¥{item['price']:,}",
                f"¥{market_price:,}",
                f"{ratio:.0%}",
                item["url"],
            ),
            tags=("bargain_row",),
        )
        self._tree.tag_configure("bargain_row", foreground="#27AE60")

    def _open_selected_url(self, _event):
        sel = self._tree.selection()
        if sel:
            values = self._tree.item(sel[0], "values")
            url = values[4]
            if url.startswith("http"):
                webbrowser.open(url)

    def _log(self, msg: str, tag: str = "info"):
        ts = time.strftime("%H:%M:%S")

        def _insert():
            self._log_box.insert(tk.END, f"[{ts}] {msg}\n", tag)
            self._log_box.see(tk.END)

        self.root.after(0, _insert)
        logger.info(msg)

    def _set_status(self, text: str):
        self.root.after(0, lambda: self._status_var.set(text))

    def _reset_buttons(self):
        self._start_btn.config(state=tk.NORMAL)
        self._stop_btn.config(state=tk.DISABLED)
        self._set_status("待機中")

    # ------------------------------------------------------------------ #
    #  起動
    # ------------------------------------------------------------------ #

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    app = BargainDetectorApp()
    app.run()
