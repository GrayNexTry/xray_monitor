"""Персистентный учёт трафика по периодам (день / неделя / месяц).

Файл JSON хранится на диске и выживает перезапуск TUI и xray.
Формат:
{
  "today_base": {
    "date": "2026-03-25",
    "abs":  {"email@tag": {"uplink": 0, "downlink": 0}, ...},  // baseline на начало дня
    "pre":  {"email@tag": {"up": 0, "dn": 0}, ...}             // накоплено до последнего рестарта xray
  },
  "days": {
    "2026-03-25": {"email@tag": {"up": 1234, "dn": 5678}, ...},
    ...
  }
}
"""

from __future__ import annotations

import json
import os
import threading
from datetime import date, timedelta
from typing import Dict

_LOCK = threading.Lock()
_SAVE_EVERY = 30   # сохранять на диск каждые N тиков (~1 мин при интервале 2 с)

_DEFAULT_PATH = os.environ.get(
    "XRAY_MONITOR_DATA",
    "/opt/xray-monitor/traffic_history.json",
)


class TrafficLog:
    """Сохраняет ежедневные снэпшоты трафика xray и считает периоды."""

    def __init__(self, path: str = _DEFAULT_PATH) -> None:
        self.path       = path
        self._data: dict = {"today_base": {}, "days": {}}
        self._last_abs: dict = {}   # последние абс. значения от gRPC
        self._tick_n    = 0
        self._load()

    # ── Диск ────────────────────────────────────────────────

    def _load(self) -> None:
        try:
            if os.path.exists(self.path):
                with open(self.path) as f:
                    self._data = json.load(f)
        except Exception:
            self._data = {"today_base": {}, "days": {}}

    def _save(self) -> None:
        try:
            d = os.path.dirname(self.path)
            if d:
                os.makedirs(d, exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self._data, f, ensure_ascii=False)
            os.replace(tmp, self.path)
        except Exception:
            pass

    # ── Вычисление ──────────────────────────────────────────

    def _day_totals(self, abs_vals: dict, today_base: dict) -> dict:
        """Вычисляет итоговый трафик за сегодня = pre + (abs - base)."""
        base_abs = today_base.get("abs", {})
        pre      = today_base.get("pre", {})
        result: dict = {}
        for em, vals in abs_vals.items():
            b    = base_abs.get(em, {})
            d_up = max(0, vals.get("uplink",   0) - b.get("uplink",   0))
            d_dn = max(0, vals.get("downlink", 0) - b.get("downlink", 0))
            p    = pre.get(em, {})
            result[em] = {
                "up": p.get("up", 0) + d_up,
                "dn": p.get("dn", 0) + d_dn,
            }
        return result

    # ── Публичный API ────────────────────────────────────────

    def update(self, users_abs: dict) -> None:
        """Вызывается каждый тик.
        users_abs: {"email@tag": {"uplink": int, "downlink": int}} — абс. счётчики gRPC.
        """
        if not users_abs:
            return
        today = date.today().isoformat()

        with _LOCK:
            tb = self._data.get("today_base", {})

            # ── Новый день ───────────────────────────────────
            if tb.get("date") != today:
                prev_date = tb.get("date")
                if prev_date and self._last_abs:
                    # Финализируем вчера
                    self._data["days"][prev_date] = self._day_totals(
                        self._last_abs, tb
                    )
                # Инициализируем базу для нового дня
                self._data["today_base"] = {
                    "date": today,
                    "abs":  {em: dict(v) for em, v in users_abs.items()},
                    "pre":  {},
                }
                tb = self._data["today_base"]
                # Ротация: храним не более 90 дней
                days = sorted(self._data["days"].keys())
                for old in days[:-90]:
                    del self._data["days"][old]
                self._last_abs = {em: dict(v) for em, v in users_abs.items()}
                self._save()
                return

            base_abs = tb.get("abs", {})

            # ── Детект рестарта xray (счётчики упали) ────────
            restarted = any(
                users_abs.get(em, {}).get("downlink", 0) < v.get("downlink", 0) or
                users_abs.get(em, {}).get("uplink",   0) < v.get("uplink",   0)
                for em, v in base_abs.items()
                if v.get("downlink", 0) > 102400  # игнорируем <100 KB
            )

            if restarted and self._last_abs:
                # Накапливаем дельту до рестарта в pre
                pre = tb.setdefault("pre", {})
                for em, vals in self._last_abs.items():
                    b    = base_abs.get(em, {})
                    d_up = max(0, vals.get("uplink",   0) - b.get("uplink",   0))
                    d_dn = max(0, vals.get("downlink", 0) - b.get("downlink", 0))
                    if em not in pre:
                        pre[em] = {"up": 0, "dn": 0}
                    pre[em]["up"] += d_up
                    pre[em]["dn"] += d_dn
                # Новый baseline = текущие значения после рестарта
                tb["abs"] = {em: dict(v) for em, v in users_abs.items()}
                base_abs  = tb["abs"]

            # ── Сохраняем сегодня ────────────────────────────
            self._data["days"][today] = self._day_totals(users_abs, tb)
            self._last_abs = {em: dict(v) for em, v in users_abs.items()}

            # Сохраняем на диск периодически
            self._tick_n += 1
            if self._tick_n % _SAVE_EVERY == 0:
                self._save()

    def get_today(self) -> Dict[str, dict]:
        today = date.today().isoformat()
        with _LOCK:
            return dict(self._data.get("days", {}).get(today, {}))

    def get_period(self, n_days: int) -> Dict[str, dict]:
        """Суммарный трафик за последние N дней."""
        result: Dict[str, dict] = {}
        cutoff = (date.today() - timedelta(days=n_days - 1)).isoformat()
        with _LOCK:
            for day, users in self._data.get("days", {}).items():
                if day < cutoff:
                    continue
                for em, v in users.items():
                    if em not in result:
                        result[em] = {"up": 0, "dn": 0}
                    result[em]["up"] += v.get("up", 0)
                    result[em]["dn"] += v.get("dn", 0)
        return result

    def get_weekly(self) -> Dict[str, dict]:
        return self.get_period(7)

    def get_monthly(self) -> Dict[str, dict]:
        return self.get_period(30)

    def available_days(self) -> int:
        with _LOCK:
            return len(self._data.get("days", {}))
