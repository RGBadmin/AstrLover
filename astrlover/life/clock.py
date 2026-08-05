"""A5 时间感知：她知道现在几点、周几、什么节日、认识多少天了。

所有时序拟真的地基。农历节日经 zhdate 计算（缺依赖时静默跳过农历节）。
"""

from datetime import date, datetime, timedelta

try:
    from zoneinfo import ZoneInfo
except ImportError:  # 理论上 3.9+ 均有
    ZoneInfo = None

try:
    from zhdate import ZhDate
except ImportError:
    ZhDate = None

_WEEK_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

_SOLAR_FESTIVALS = {
    (1, 1): "元旦",
    (2, 14): "情人节",
    (3, 8): "妇女节",
    (5, 1): "劳动节",
    (5, 20): "520",
    (6, 1): "儿童节",
    (10, 1): "国庆节",
    (12, 24): "平安夜",
    (12, 25): "圣诞节",
}

# 农历节日：(月, 日) -> 名称
_LUNAR_FESTIVALS = {
    (1, 1): "春节",
    (1, 15): "元宵节",
    (5, 5): "端午节",
    (7, 7): "七夕",
    (8, 15): "中秋节",
    (9, 9): "重阳节",
}


def _lunar_to_solar(year: int, month: int, day: int) -> date | None:
    if ZhDate is None:
        return None
    try:
        return ZhDate(year, month, day).to_datetime().date()
    except (ValueError, TypeError, KeyError):
        return None


class Clock:
    def __init__(self, tz_name: str = "Asia/Shanghai"):
        self.tz = None
        if ZoneInfo is not None:
            try:
                self.tz = ZoneInfo(tz_name)
            except Exception:
                self.tz = None

    def now(self) -> datetime:
        return datetime.now(self.tz) if self.tz else datetime.now()

    def today(self) -> date:
        return self.now().date()

    def today_str(self) -> str:
        return self.today().isoformat()

    def week_str(self) -> str:
        iso = self.today().isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"

    def weekday_cn(self) -> str:
        return _WEEK_CN[self.now().weekday()]

    def is_weekend(self) -> bool:
        return self.now().weekday() >= 5

    def time_slot(self) -> str:
        h = self.now().hour
        if h < 5:
            return "凌晨"
        if h < 8:
            return "清晨"
        if h < 11:
            return "上午"
        if h < 13:
            return "中午"
        if h < 17:
            return "下午"
        if h < 19:
            return "傍晚"
        if h < 23:
            return "晚上"
        return "深夜"

    # ---- 节日 ----
    def festivals_on(self, d: date) -> list[str]:
        found = []
        name = _SOLAR_FESTIVALS.get((d.month, d.day))
        if name:
            found.append(name)
        for (lm, ld), lname in _LUNAR_FESTIVALS.items():
            solar = _lunar_to_solar(d.year, lm, ld)
            if solar == d:
                found.append(lname)
        # 除夕 = 春节前一天
        spring = _lunar_to_solar(d.year + 1, 1, 1) or _lunar_to_solar(d.year, 1, 1)
        if spring and d == spring - timedelta(days=1):
            found.append("除夕")
        return found

    # ---- 纪念日与天数 ----
    @staticmethod
    def _parse_date(s: str) -> date | None:
        try:
            return date.fromisoformat(str(s).strip())
        except (ValueError, TypeError):
            return None

    def days_since(self, s: str) -> int | None:
        d = self._parse_date(s)
        return (self.today() - d).days if d else None

    def upcoming_specials(self, milestones: list[dict], birthday: str, within_days: int = 3) -> list[str]:
        """今天或近几天内的特殊日子（周年按月日循环）。"""
        today = self.today()
        found: list[str] = []

        def check_annual(month: int, day: int, title: str):
            for offset in range(within_days + 1):
                d = today + timedelta(days=offset)
                if d.month == month and d.day == day:
                    when = "今天" if offset == 0 else f"{offset}天后"
                    found.append(f"{when}是{title}")
                    return

        bd = self._parse_date(birthday)
        if bd:
            check_annual(bd.month, bd.day, "你的生日")
        for m in milestones or []:
            md = self._parse_date(m.get("date", ""))
            if not md:
                continue
            if m.get("recurring", True):
                check_annual(md.month, md.day, str(m.get("title", "纪念日")))
            elif md == today:
                found.append(f"今天是{m.get('title', '纪念日')}")
        return found

    def describe_now(self, met_on: str = "", anniversary: str = "") -> str:
        """给 system prompt 的时间感知块。"""
        n = self.now()
        lines = [
            f"现在是{n.year}年{n.month}月{n.day}日 {self.weekday_cn()} "
            f"{n.hour:02d}:{n.minute:02d}（{self.time_slot()}）。"
        ]
        fests = self.festivals_on(n.date())
        if fests:
            lines.append("今天是" + "、".join(fests) + "。")
        met_days = self.days_since(met_on)
        if met_days is not None and met_days >= 0:
            lines.append(f"你们认识第{met_days + 1}天。")
        ann_days = self.days_since(anniversary)
        if ann_days is not None and ann_days >= 0:
            lines.append(f"确定关系第{ann_days + 1}天。")
        return "".join(lines)
