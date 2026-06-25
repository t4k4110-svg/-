# ============================================================
# 通知モジュール（音 + ポップアップウィンドウ）
# Windows標準のwinsoundとtkinterを使うので追加インストール不要
# ============================================================

import threading
import webbrowser
import winsound
import tkinter as tk
from tkinter import font as tkfont
from config import PRICE_RATIO_THRESHOLD


def notify_bargain(title: str, price: int, market_price: int, ratio: float, url: str):
    """
    割安商品を発見したときに呼び出す。
    ・警告音を3回鳴らす
    ・ポップアップウィンドウを最前面に表示する
    GUIブロックを防ぐため別スレッドで実行する。
    """
    t = threading.Thread(
        target=_show_popup,
        args=(title, price, market_price, ratio, url),
        daemon=True,
    )
    t.start()


# ------------------------------------------------------------------ #
#  内部実装
# ------------------------------------------------------------------ #

def _play_alert():
    """警告ビープ音を鳴らす（Windows標準winsound使用）"""
    try:
        for _ in range(3):
            winsound.Beep(1200, 250)   # 高音 0.25秒
            winsound.Beep(800, 150)    # 低音 0.15秒
        winsound.Beep(1200, 600)       # 最後に長め
    except Exception:
        # winsoundが使えない環境でもクラッシュしない
        pass


def _show_popup(title: str, price: int, market_price: int, ratio: float, url: str):
    """ポップアップウィンドウを表示"""
    # 音は別スレッドで即再生（ポップアップ生成より先）
    sound_thread = threading.Thread(target=_play_alert, daemon=True)
    sound_thread.start()

    root = tk.Tk()
    root.title("⚠ 割安商品発見!")
    root.geometry("480x320")
    root.resizable(False, False)
    root.attributes("-topmost", True)   # 常に最前面

    # ── 背景色 ──
    BG_RED   = "#C0392B"
    BG_WHITE = "#FFFFFF"
    BG_BTN_GREEN = "#27AE60"
    BG_BTN_GRAY  = "#7F8C8D"

    root.configure(bg=BG_RED)

    # ── ヘッダー ──
    header = tk.Label(
        root,
        text="⚠  割安商品を発見しました！",
        font=("Meiryo UI", 15, "bold"),
        bg=BG_RED, fg="white",
        pady=12,
    )
    header.pack(fill=tk.X)

    # ── 情報カード ──
    card = tk.Frame(root, bg=BG_WHITE, padx=18, pady=12)
    card.pack(fill=tk.X, padx=18, pady=4)

    short_title = title[:42] + ("…" if len(title) > 42 else "")

    _card_row(card, "商品名", short_title, BG_WHITE, bold=False)
    _card_row(card, "出品価格",  f"¥{price:,}", BG_WHITE, fg_val="#C0392B", bold=True)
    _card_row(card, "相場価格",  f"¥{market_price:,}", BG_WHITE)
    _card_row(
        card,
        "相場比",
        f"{ratio:.0%}  ← 閾値 {PRICE_RATIO_THRESHOLD:.0%} 以下！",
        BG_WHITE, fg_val="#27AE60", bold=True,
    )

    # ── ボタン ──
    btn_frame = tk.Frame(root, bg=BG_RED, pady=14)
    btn_frame.pack()

    def open_and_close():
        webbrowser.open(url)
        root.destroy()

    tk.Button(
        btn_frame,
        text="  商品ページを開く  ",
        command=open_and_close,
        bg=BG_BTN_GREEN, fg="white",
        font=("Meiryo UI", 11, "bold"),
        relief=tk.FLAT, padx=8, pady=6, cursor="hand2",
    ).pack(side=tk.LEFT, padx=6)

    tk.Button(
        btn_frame,
        text="  閉じる  ",
        command=root.destroy,
        bg=BG_BTN_GRAY, fg="white",
        font=("Meiryo UI", 11),
        relief=tk.FLAT, padx=8, pady=6, cursor="hand2",
    ).pack(side=tk.LEFT, padx=6)

    # 自動で30秒後に閉じる
    root.after(30_000, root.destroy)
    root.mainloop()


def _card_row(parent, label: str, value: str, bg: str,
              fg_val: str = "#2C3E50", bold: bool = False):
    """カード内の1行を作成"""
    row = tk.Frame(parent, bg=bg)
    row.pack(fill=tk.X, pady=2)
    tk.Label(row, text=f"{label}:", font=("Meiryo UI", 9),
             bg=bg, fg="#7F8C8D", width=8, anchor="w").pack(side=tk.LEFT)
    weight = "bold" if bold else "normal"
    tk.Label(row, text=value, font=("Meiryo UI", 10, weight),
             bg=bg, fg=fg_val, anchor="w").pack(side=tk.LEFT)
