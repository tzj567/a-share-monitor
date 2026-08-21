"""Dependency-light Tk canvas chart for closed A-share daily bars."""

from __future__ import annotations

from tkinter import BOTH, Canvas
from tkinter import ttk

import pandas as pd


class StockChart(ttk.Frame):
    def __init__(self, parent) -> None:
        super().__init__(parent)
        self.price = Canvas(self, height=300, background="#ffffff", highlightthickness=1, highlightbackground="#c9ced6")
        self.volume = Canvas(self, height=115, background="#ffffff", highlightthickness=1, highlightbackground="#c9ced6")
        self.price.pack(fill=BOTH, expand=True)
        self.volume.pack(fill="x", pady=(5, 0))
        self._frame = pd.DataFrame()
        self.price.bind("<Configure>", lambda _event: self._draw())
        self.volume.bind("<Configure>", lambda _event: self._draw())
        self.price.bind("<Motion>", self._hover)
        self.price.bind("<Leave>", lambda _event: self.price.delete("hover"))

    def render(self, frame: pd.DataFrame) -> None:
        self._frame = frame.copy()
        self._draw()

    def clear(self, message: str = "暂无已闭合行情，请先同步数据") -> None:
        self._frame = pd.DataFrame()
        self.price.delete("all")
        self.volume.delete("all")
        width = max(self.price.winfo_width(), 700)
        self.price.create_text(width / 2, 130, text=message, fill="#5f6b7a", font=("Microsoft YaHei UI", 11))

    def _prepared(self) -> tuple[pd.DataFrame, str]:
        frame = self._frame.copy()
        if frame.empty:
            return frame, "close"
        frame = frame.sort_values("timestamp_ms" if "timestamp_ms" in frame else "timestamp").reset_index(drop=True)
        basis = "qfq_close" if "qfq_close" in frame and not frame["qfq_close"].isna().any() else "close"
        prefix = "qfq_" if basis == "qfq_close" else ""
        for source, target in ((f"{prefix}open", "plot_open"), (f"{prefix}high", "plot_high"), (f"{prefix}low", "plot_low"), (f"{prefix}close", "plot_close")):
            frame[target] = frame[source].astype(float)
        frame["ma5"] = frame["plot_close"].rolling(5, min_periods=5).mean()
        frame["ma20"] = frame["plot_close"].rolling(20, min_periods=20).mean()
        return frame, basis

    def _draw(self) -> None:
        self.price.delete("all")
        self.volume.delete("all")
        frame, basis = self._prepared()
        if frame.empty:
            self.clear()
            return
        width = max(self.price.winfo_width(), 700)
        height = max(self.price.winfo_height(), 260)
        vwidth = max(self.volume.winfo_width(), 700)
        vheight = max(self.volume.winfo_height(), 100)
        left, right, top, bottom = 66, 18, 24, 28
        plot_w = max(1, width - left - right)
        plot_h = max(1, height - top - bottom)
        lows = frame["plot_low"]
        highs = frame["plot_high"]
        minimum, maximum = float(lows.min()), float(highs.max())
        padding = max((maximum - minimum) * 0.08, maximum * 0.005)
        minimum -= padding
        maximum += padding
        span = max(maximum - minimum, 1e-9)
        step = plot_w / max(len(frame), 1)

        def x(index: int) -> float:
            return left + (index + 0.5) * step

        def y(value: float) -> float:
            return top + (maximum - value) / span * plot_h

        for grid_index in range(5):
            value = maximum - span * grid_index / 4
            yy = y(value)
            self.price.create_line(left, yy, width - right, yy, fill="#e5e8ed")
            self.price.create_text(left - 7, yy, text=f"{value:.2f}", anchor="e", fill="#5f6b7a", font=("Segoe UI", 9))
        self.price.create_text(left, 10, text=f"K线（{'前复权' if basis == 'qfq_close' else '原始价格'}）  MA5  MA20", anchor="w", fill="#303640", font=("Microsoft YaHei UI", 9))

        body_width = max(1, min(step * 0.58, 8))
        for index, row in frame.iterrows():
            up = row["plot_close"] >= row["plot_open"]
            color = "#d84a3a" if up else "#16855b"
            xx = x(index)
            self.price.create_line(xx, y(row["plot_high"]), xx, y(row["plot_low"]), fill=color)
            y_open, y_close = y(row["plot_open"]), y(row["plot_close"])
            self.price.create_rectangle(xx - body_width / 2, min(y_open, y_close), xx + body_width / 2, max(y_open, y_close) + 1, outline=color, fill=color)
        for column, color in (("ma5", "#d98618"), ("ma20", "#5d5bd6")):
            points: list[float] = []
            for index, value in enumerate(frame[column]):
                if pd.notna(value):
                    points.extend((x(index), y(float(value))))
            if len(points) >= 4:
                self.price.create_line(*points, fill=color, width=2, smooth=True)

        tick_indexes = sorted(set([0, len(frame) // 2, len(frame) - 1]))
        for index in tick_indexes:
            label = str(frame.iloc[index]["trading_date"])[5:]
            self.price.create_text(x(index), height - 9, text=label, fill="#5f6b7a", font=("Segoe UI", 9))

        volumes = frame["volume"].astype(float)
        vmax = max(float(volumes.max()), 1.0)
        vtop, vbottom = 10, 22
        vplot_h = max(vheight - vtop - vbottom, 1)
        for index, row in frame.iterrows():
            previous = frame.iloc[index - 1]["plot_close"] if index else row["plot_open"]
            color = "#d84a3a" if row["plot_close"] >= previous else "#16855b"
            xx = left + (index + 0.5) * ((vwidth - left - right) / max(len(frame), 1))
            bar_height = float(row["volume"]) / vmax * vplot_h
            self.volume.create_rectangle(xx - body_width / 2, vheight - vbottom - bar_height, xx + body_width / 2, vheight - vbottom, outline=color, fill=color)
        self.volume.create_text(left, 7, text="成交量（股）", anchor="w", fill="#303640", font=("Microsoft YaHei UI", 9))

        self._hover_points = [(x(index), row) for index, row in frame.iterrows()]
        self._chart_bounds = (left, width - right, top, height - bottom)

    def _hover(self, event) -> None:
        if not getattr(self, "_hover_points", None):
            return
        left, right, top, bottom = self._chart_bounds
        if not left <= event.x <= right or not top <= event.y <= bottom:
            self.price.delete("hover")
            return
        xx, row = min(self._hover_points, key=lambda item: abs(item[0] - event.x))
        self.price.delete("hover")
        self.price.create_line(xx, top, xx, bottom, fill="#7b8492", dash=(3, 3), tags="hover")
        label = f"{row['trading_date']}  开 {row['plot_open']:.2f}  高 {row['plot_high']:.2f}  低 {row['plot_low']:.2f}  收 {row['plot_close']:.2f}"
        anchor = "nw" if xx < (left + right) / 2 else "ne"
        label_x = xx + 6 if anchor == "nw" else xx - 6
        self.price.create_text(label_x, top + 5, text=label, anchor=anchor, fill="#20242b", font=("Microsoft YaHei UI", 9), tags="hover")
