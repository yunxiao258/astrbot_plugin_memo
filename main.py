# -*- coding: utf-8 -*-
"""AstrBot 群聊备忘录与定时提醒插件：记录备忘、一次性/周期定时提醒"""

import asyncio
import json
import os
import re
from datetime import date, datetime, timedelta

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.message_components import Plain
from astrbot.api.star import Context, Star, register
from astrbot.api.all import MessageChain

# 插件元数据
PLUGIN_NAME = "astrbot_plugin_memo"
PLUGIN_AUTHOR = "云晓"
PLUGIN_DESC = "群聊备忘录与定时提醒"
PLUGIN_VERSION = "1.0.0"

# 时间解析正则：24 小时制 HH:MM
# 一次性提醒：`HH:MM <内容>`
TIME_RE = re.compile(r"^\s*(\d{1,2}):(\d{1,2})\s+(.+?)\s*$")
# 周期提醒：`每(天|周) HH:MM <内容>`
PERIOD_RE = re.compile(r"^\s*每(天|周)\s+(\d{1,2}):(\d{1,2})\s+(.+?)\s*$")

# 提醒类型常量
TYPE_ONCE = "once"      # 一次性提醒
TYPE_DAILY = "daily"    # 每天提醒
TYPE_WEEKLY = "weekly"  # 每周提醒

# ========== 农历数据（1900-2100） ==========
# 每项为农历年信息：低 4 位为闰月月份（0 表示无闰月），
# 位 4 起从左到右依次表示正月到腊月是否为大月（30 天），
# 位 16 表示闰月是否为大月。
LUNAR_INFO = [
    0x04bd8, 0x04ae0, 0x0a570, 0x054d5, 0x0d260, 0x0d950, 0x16554, 0x056a0, 0x09ad0, 0x055d2,  # 1900-1909
    0x04ae0, 0x0a5b6, 0x0a4d0, 0x0d250, 0x1d255, 0x0b540, 0x0d6a0, 0x0ada2, 0x095b0, 0x14977,  # 1910-1919
    0x04970, 0x0a4b0, 0x0b4b5, 0x06a50, 0x06d40, 0x1ab54, 0x02b60, 0x09570, 0x052f2, 0x04970,  # 1920-1929
    0x06566, 0x0d4a0, 0x0ea50, 0x06e95, 0x05ad0, 0x02b60, 0x186e3, 0x092e0, 0x1c8d7, 0x0c950,  # 1930-1939
    0x0d4a0, 0x1d8a6, 0x0b550, 0x056a0, 0x1a5b4, 0x025d0, 0x092d0, 0x0d2b2, 0x0a950, 0x0b557,  # 1940-1949
    0x06ca0, 0x0b550, 0x15355, 0x04da0, 0x0a5b0, 0x14573, 0x052b0, 0x0a9a8, 0x0e950, 0x06aa0,  # 1950-1959
    0x0aea6, 0x0ab50, 0x04b60, 0x0aae4, 0x0a570, 0x05260, 0x0f263, 0x0d950, 0x05b57, 0x056a0,  # 1960-1969
    0x096d0, 0x04dd5, 0x04ad0, 0x0a4d0, 0x0d4d4, 0x0d250, 0x0d558, 0x0b540, 0x0b6a0, 0x195a6,  # 1970-1979
    0x095b0, 0x049b0, 0x0a974, 0x0a4b0, 0x0b27a, 0x06a50, 0x06d40, 0x0af46, 0x0ab60, 0x09570,  # 1980-1989
    0x04af5, 0x04970, 0x064b0, 0x074a3, 0x0ea50, 0x06b58, 0x05ac0, 0x0ab60, 0x096d5, 0x092e0,  # 1990-1999
    0x0c960, 0x0d954, 0x0d4a0, 0x0da50, 0x07552, 0x056a0, 0x0abb7, 0x025d0, 0x092d0, 0x0cab5,  # 2000-2009
    0x0a950, 0x0b4a0, 0x0baa4, 0x0ad50, 0x055d9, 0x04ba0, 0x0a5b0, 0x15176, 0x052b0, 0x0a930,  # 2010-2019
    0x07954, 0x06aa0, 0x0ad50, 0x05b52, 0x04b60, 0x0a6e6, 0x0a4e0, 0x0d260, 0x0ea65, 0x0d530,  # 2020-2029
    0x05aa0, 0x076a3, 0x096d0, 0x04afb, 0x04ad0, 0x0a4d0, 0x1d0b6, 0x0d250, 0x0d520, 0x0dd45,  # 2030-2039
    0x0b5a0, 0x056d0, 0x055b2, 0x049b0, 0x0a577, 0x0a4b0, 0x0aa50, 0x1b255, 0x06d20, 0x0ada0,  # 2040-2049
    0x14b63, 0x09370, 0x049f8, 0x04970, 0x064b0, 0x168a6, 0x0ea50, 0x06b20, 0x1a6c4, 0x0aae0,  # 2050-2059
    0x0a2e0, 0x0d2e3, 0x0c960, 0x0d557, 0x0d4a0, 0x0da50, 0x05d55, 0x056a0, 0x0a6d0, 0x055d4,  # 2060-2069
    0x052d0, 0x0a9b8, 0x0a950, 0x0b4a0, 0x0b6a6, 0x0ad50, 0x055a0, 0x0aba4, 0x0a5b0, 0x052b0,  # 2070-2079
    0x0b273, 0x06930, 0x07337, 0x06aa0, 0x0ad50, 0x14b55, 0x04b60, 0x0a570, 0x054e4, 0x0d160,  # 2080-2089
    0x0e968, 0x0d520, 0x0daa0, 0x16aa6, 0x056d0, 0x04ae0, 0x0a9d4, 0x0a2d0, 0x0d150, 0x0f252,  # 2090-2099
    0x0d520,  # 2100
]

LUNAR_MONTH_NAMES = ["正", "二", "三", "四", "五", "六", "七", "八", "九", "十", "冬", "腊"]


def _lunar_months(year: int) -> tuple[list[int], int]:
    """返回农历年各月天数列表（含闰月排在对应月之后）与闰月月份（0 表示无闰月）"""
    if not 1900 <= year <= 2100:
        return [], 0
    info = LUNAR_INFO[year - 1900]
    leap = info & 0xF
    # 月份位编码：0x10000>>m（m=1..12，正月=0x8000...腊月=0x8），位 0x10000 为闰月
    months = [30 if (info & (0x10000 >> (i + 1))) else 29 for i in range(12)]
    if leap:
        months.insert(leap, 30 if (info & 0x10000) else 29)
    return months, leap


def _lunar_to_solar(lunar_year: int, lunar_month: int, lunar_day: int,
                    is_leap: bool = False) -> date | None:
    """农历转公历；非法输入返回 None"""
    if not 1900 <= lunar_year <= 2100:
        return None
    months, leap = _lunar_months(lunar_year)
    if is_leap:
        # 闰月只能对应当年的闰月月份
        if leap != lunar_month:
            return None
        index = lunar_month
    else:
        # 正常月：闰月插入在相同编号月之后，索引需后移
        index = lunar_month - 1
        if leap and index >= leap:
            index += 1
    if not (0 <= index < len(months)):
        return None
    if not (1 <= lunar_day <= months[index]):
        return None
    offset = sum(months[:index]) + lunar_day - 1
    # 累加 1900 年至 lunar_year-1 的全部农历年天数
    for y in range(1900, lunar_year):
        offset += sum(_lunar_months(y)[0])
    return date(1900, 1, 31) + timedelta(days=offset)


def _solar_to_lunar(solar_year: int, solar_month: int, solar_day: int):
    """公历转农历；返回 (农历年, 农历月, 农历日, 是否闰月)；非法输入返回 None"""
    try:
        d = date(solar_year, solar_month, solar_day)
    except ValueError:
        return None
    base = date(1900, 1, 31)
    if d < base:
        return None
    offset = (d - base).days
    ly = 1900
    while ly <= 2100:
        months, _ = _lunar_months(ly)
        days = sum(months)
        if offset < days:
            break
        offset -= days
        ly += 1
    if ly > 2100:
        return None
    months, leap = _lunar_months(ly)
    for i, mdays in enumerate(months):
        if offset < mdays:
            if leap and i == leap:
                return (ly, leap, offset + 1, True)
            # 无闰月：月份 = 索引 + 1；有闰月：索引在闰月之后需顺延
            lm = i + 1 if (not leap or i < leap) else i
            return (ly, lm, offset + 1, False)
        offset -= mdays
    return None


def _lunar_month_name(month: int) -> str:
    """农历月中文名（如 1 -> 正月，12 -> 腊月）"""
    if 1 <= month <= 12:
        return LUNAR_MONTH_NAMES[month - 1] + "月"
    return f"{month}月"


def _cn_to_int(text: str):
    """中文数字转阿拉伯数字（支持 一~三十、十/廿 组合）；无法解析返回 None"""
    if not isinstance(text, str) or not text:
        return None
    digits = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "廿": 20}
    try:
        return int(text)
    except ValueError:
        pass
    if len(text) == 1:
        return digits.get(text)
    if len(text) == 2:
        if text[0] == "十":
            return 10 + digits.get(text[1], 0)      # 十一~十九
        if text[0] == "廿":
            return 20 + digits.get(text[1], 0)      # 廿一~廿九
        if text[1] == "十":
            return digits.get(text[0], 0) * 10      # 二十~九十
        return None
    if len(text) == 3 and text[1] == "十":           # 二十一~三十九（仅到三十）
        return digits.get(text[0], 0) * 10 + digits.get(text[2], 0)
    return None


def _safe_int(value, default):
    """配置脏值保护：安全转换为 int，失败回退默认值"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value, default):
    """配置脏值保护：安全转换为 float，失败回退默认值"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _valid_time(hour, minute):
    """校验时分是否合法（hour 0-23，minute 0-59）"""
    return 0 <= hour <= 23 and 0 <= minute <= 59


@register(PLUGIN_NAME, PLUGIN_AUTHOR, PLUGIN_DESC, PLUGIN_VERSION)
class MemoPlugin(Star):
    """群聊备忘录与定时提醒插件"""

    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.config = config or {}
        # 数据目录：plugin_data/astrbot_plugin_memo
        self.data_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "plugin_data",
            PLUGIN_NAME,
        )
        os.makedirs(self.data_dir, exist_ok=True)

        # 内存数据结构：key = 会话 UMO
        self._memos: dict[str, list[dict]] = {}
        self._reminders: dict[str, list[dict]] = {}
        self._birthdays: dict[str, list[dict]] = {}
        self._countdowns: dict[str, list[dict]] = {}
        self._habits: dict[str, list[dict]] = {}
        self._load_memos()
        self._load_reminders()
        self._load_birthdays()
        self._load_countdowns()
        self._load_habits()

        # 后台提醒任务
        self._reminder_task: asyncio.Task | None = None
        self._reminder_running = False

        logger.info(f"【{PLUGIN_NAME}】插件初始化完成")

    # ========== 持久化 ==========

    def _memos_file(self) -> str:
        return os.path.join(self.data_dir, "memos.json")

    def _reminders_file(self) -> str:
        return os.path.join(self.data_dir, "reminders.json")

    def _load_memos(self):
        """从磁盘加载备忘（校验结构，损坏/非预期格式时重置）"""
        self._memos = self._load_json(self._memos_file(), dict)

    def _load_reminders(self):
        """从磁盘加载提醒（校验结构，损坏/非预期格式时重置）"""
        self._reminders = self._load_json(self._reminders_file(), dict)

    def _load_json(self, path, expected_type):
        """加载 JSON 文件并校验顶层类型，异常时返回空容器"""
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, expected_type):
                    return data
                logger.warning(f"【{PLUGIN_NAME}】数据文件格式异常，已重置: {path}")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"【{PLUGIN_NAME}】加载数据失败: {e}")
        return {}

    def _save_memos(self):
        """保存备忘到磁盘"""
        self._save_json(self._memos_file(), self._memos)

    def _save_reminders(self):
        """保存提醒到磁盘"""
        self._save_json(self._reminders_file(), self._reminders)

    # ========== 生日持久化 ==========

    def _birthdays_file(self) -> str:
        return os.path.join(self.data_dir, "birthdays.json")

    def _countdowns_file(self) -> str:
        return os.path.join(self.data_dir, "countdowns.json")

    def _habits_file(self) -> str:
        return os.path.join(self.data_dir, "habits.json")

    def _load_birthdays(self):
        """从磁盘加载生日（校验结构，损坏时重置）"""
        self._birthdays = self._load_json(self._birthdays_file(), dict)

    def _load_countdowns(self):
        """从磁盘加载倒计时（校验结构，损坏时重置）"""
        self._countdowns = self._load_json(self._countdowns_file(), dict)

    def _load_habits(self):
        """从磁盘加载习惯打卡（校验结构，损坏时重置）"""
        self._habits = self._load_json(self._habits_file(), dict)

    def _save_birthdays(self):
        """保存生日到磁盘"""
        self._save_json(self._birthdays_file(), self._birthdays)

    def _save_countdowns(self):
        """保存倒计时到磁盘"""
        self._save_json(self._countdowns_file(), self._countdowns)

    def _save_habits(self):
        """保存习惯打卡到磁盘"""
        self._save_json(self._habits_file(), self._habits)

    def _save_json(self, path, data):
        """保存 JSON 文件（临时文件 + 原子替换防损坏；写入失败不影响内存数据）"""
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"【{PLUGIN_NAME}】保存数据失败: {e}")

    # ========== 工具 ==========

    def _now(self) -> datetime:
        """当前时间（测试可 monkeypatch 覆盖）"""
        return datetime.now()

    def _check_interval(self) -> int:
        """后台检查间隔（秒），脏值回退默认 60"""
        return max(1, _safe_int(self.config.get("check_interval", 60), 60))

    def _max_memos(self) -> int:
        """每个会话最大备忘条数，脏值回退默认 100"""
        return max(1, _safe_int(self.config.get("max_memos", 100), 100))

    def _max_reminders(self) -> int:
        """每个会话最大提醒条数，脏值回退默认 20"""
        return max(1, _safe_int(self.config.get("max_reminders", 20), 20))

    def _timeout_warn_seconds(self) -> int:
        """一次性提醒超时容忍秒数，脏值回退默认 300"""
        return max(0, _safe_int(self.config.get("timeout_warn_seconds", 300), 300))

    def _fmt_time(self, dt: datetime) -> str:
        """格式化时间为可读字符串"""
        return dt.strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _chain(text: str):
        """构造纯文本消息链（用于主动推送）"""
        return MessageChain([Plain(text)])

    @staticmethod
    def _reply(event: AstrMessageEvent, text: str):
        """构造命令回复结果（MessageEventResult，框架要求）"""
        return event.chain_result([Plain(text)])

    # ========== 备忘逻辑 ==========

    def _memo_list(self, umo: str) -> list[dict]:
        """获取指定会话的备忘列表（确保存在）"""
        if umo not in self._memos or not isinstance(self._memos[umo], list):
            self._memos[umo] = []
        return self._memos[umo]

    def add_memo(self, umo: str, content: str) -> dict:
        """新增一条备忘，返回该条数据；超限自动丢弃最旧"""
        items = self._memo_list(umo)
        max_memos = self._max_memos()
        while len(items) >= max_memos:
            items.pop(0)
        idx = self._next_id(items)
        item = {
            "id": idx,
            "content": content,
            "time": self._fmt_time(self._now()),
        }
        items.append(item)
        self._save_memos()
        return item

    def list_memos(self, umo: str) -> list[dict]:
        """查看指定会话全部备忘（含编号、内容、时间）"""
        return list(self._memo_list(umo))

    def delete_memo(self, umo: str, mid: int) -> bool:
        """按编号删除备忘，删除成功返回 True"""
        items = self._memo_list(umo)
        for i, item in enumerate(items):
            if item.get("id") == mid:
                items.pop(i)
                self._save_memos()
                return True
        return False

    @staticmethod
    def _next_id(items: list[dict]) -> int:
        """生成递增编号（当前最大 id + 1，最小从 1 开始）"""
        max_id = 0
        for it in items:
            try:
                max_id = max(max_id, int(it.get("id", 0)))
            except (TypeError, ValueError):
                continue
        return max_id + 1

    # ========== 提醒解析 ==========

    def parse_reminder(self, text: str):
        """解析提醒指令文本。

        返回 (type, hour, minute, weekday, content)；
        - 一次性：type=once，weekday=None
        - 每天：type=daily，weekday=None
        - 每周：type=weekly，weekday=0..6（周一..周日）
        无法解析或时间非法时返回 None。
        """
        if not isinstance(text, str):
            return None
        m = PERIOD_RE.match(text)
        if m:
            period, hour_s, minute_s, content = m.groups()
            hour, minute = int(hour_s), int(minute_s)
            if not _valid_time(hour, minute) or not content:
                return None
            if period == "天":
                return (TYPE_DAILY, hour, minute, None, content.strip())
            return (TYPE_WEEKLY, hour, minute, None, content.strip())
        m = TIME_RE.match(text)
        if m:
            hour_s, minute_s, content = m.groups()
            hour, minute = int(hour_s), int(minute_s)
            if not _valid_time(hour, minute) or not content:
                return None
            return (TYPE_ONCE, hour, minute, None, content.strip())
        return None

    # ========== 提醒到期判断 ==========

    @staticmethod
    def _once_next(hour: int, minute: int, now: datetime) -> datetime:
        """计算一次性提醒的下一次触发时刻（今天该时刻，已过则明天）"""
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate

    def _is_due(self, rem: dict, now: datetime) -> bool:
        """判断某条提醒当前是否到期。

        - once：next_trigger <= now 且未触发
        - daily：当前时间 >= 设定时刻，且今天尚未触发（按日期去重）
        - weekly：当前是设定的星期几、时间 >= 设定时刻，且本周期尚未触发
        """
        rtype = rem.get("type")
        try:
            hour = int(rem.get("hour", 0))
            minute = int(rem.get("minute", 0))
        except (TypeError, ValueError):
            return False
        cur_hhmm = now.strftime("%H:%M")
        target = f"{hour:02d}:{minute:02d}"

        if rtype == TYPE_ONCE:
            if rem.get("triggered"):
                return False
            try:
                nxt = datetime.fromisoformat(rem.get("next_trigger", ""))
            except (TypeError, ValueError):
                return False
            return now >= nxt

        if rtype == TYPE_DAILY:
            if cur_hhmm < target:
                return False
            # 今天是否已触发（按日期去重）
            if rem.get("last_trigger_date") == now.strftime("%Y-%m-%d"):
                return False
            return True

        if rtype == TYPE_WEEKLY:
            weekday = _safe_int(rem.get("weekday"), -1)
            if weekday < 0 or weekday != now.weekday():
                return False
            if cur_hhmm < target:
                return False
            # 本周是否已触发（按周一所在周的日期区间去重）
            if rem.get("last_trigger_week") == self._week_key(now):
                return False
            return True

        return False

    @staticmethod
    def _week_key(now: datetime) -> str:
        """生成本周去重键：本周一（周一为一周开始）的日期"""
        monday = now - timedelta(days=now.weekday())
        return monday.strftime("%Y-%m-%d")

    def add_reminder(self, umo: str, rtype: str, hour: int, minute: int,
                     weekday, content: str) -> dict:
        """新增一条提醒，返回该条数据；超限返回 None（拒绝新增）"""
        items = self._reminders.get(umo)
        if not isinstance(items, list):
            items = self._reminders[umo] = []
        if len(items) >= self._max_reminders():
            return None
        idx = self._next_id(items)
        rem = {
            "id": idx,
            "type": rtype,
            "hour": hour,
            "minute": minute,
            "content": content,
            "time": self._fmt_time(self._now()),
            "triggered": False,
            "last_trigger_date": None,
            "last_trigger_week": None,
            "next_trigger": None,
        }
        if rtype == TYPE_WEEKLY:
            if weekday is None:
                # 未指定星期时，默认取创建当天的星期（每周同一天同一时间）
                weekday = self._now().weekday()
            rem["weekday"] = weekday
        if rtype == TYPE_ONCE:
            rem["next_trigger"] = self._once_next(
                hour, minute, self._now()).isoformat()
        items.append(rem)
        self._save_reminders()
        return rem

    def list_reminders(self, umo: str) -> list[dict]:
        """查看指定会话全部提醒"""
        items = self._reminders.get(umo)
        if not isinstance(items, list):
            return []
        # 过滤掉已触发的一次性提醒（保持列表干净）
        return [r for r in items if not (r.get("type") == TYPE_ONCE and r.get("triggered"))]

    def delete_reminder(self, umo: str, rid: int) -> bool:
        """按编号删除提醒，删除成功返回 True"""
        items = self._reminders.get(umo)
        if not isinstance(items, list):
            return False
        for i, item in enumerate(items):
            if item.get("id") == rid:
                items.pop(i)
                self._save_reminders()
                return True
        return False

    # ========== 生日提醒 ==========

    def _birthday_list(self, umo: str) -> list[dict]:
        """获取指定会话的生日列表（确保存在）"""
        if umo not in self._birthdays or not isinstance(self._birthdays[umo], list):
            self._birthdays[umo] = []
        return self._birthdays[umo]

    @staticmethod
    def parse_birthday(text: str):
        """解析生日指令文本，返回 (名字, month, day, 是否农历) 或 None。

        支持格式：
        - `名字 2026-08-18`（公历）
        - `名字 8月18日`（公历）
        - `名字 农历 八月十五`（农历，支持中文数字）
        """
        if not isinstance(text, str):
            return None
        text = text.strip()
        m = re.match(
            r"^(?P<name>\S+)\s+农历\s+(?P<m>[一二三四五六七八九十冬腊]{1,2})月"
            r"(?P<d>[一二三四五六七八九十廿]{1,3})日?\s*$",
            text,
        )
        if m:
            month_s = m.group("m")
            if month_s == "冬":
                month = 11
            elif month_s == "腊":
                month = 12
            else:
                month = _cn_to_int(month_s)
            day = _cn_to_int(m.group("d"))
            if month and day and 1 <= month <= 12 and 1 <= day <= 30:
                return (m.group("name").strip(), month, day, True)
            return None
        m = re.match(r"^(?P<name>\S+)\s+(?P<y>\d{4})-(?P<m>\d{1,2})-(?P<d>\d{1,2})\s*$", text)
        if m:
            month, day = int(m.group("m")), int(m.group("d"))
            if 1 <= month <= 12 and 1 <= day <= 31:
                try:
                    date(int(m.group("y")), month, day)
                except ValueError:
                    return None
                return (m.group("name").strip(), month, day, False)
            return None
        m = re.match(r"^(?P<name>\S+)\s+(?P<m>\d{1,2})月(?P<d>\d{1,2})日?\s*$", text)
        if m:
            month, day = int(m.group("m")), int(m.group("d"))
            if 1 <= month <= 12 and 1 <= day <= 31:
                return (m.group("name").strip(), month, day, False)
        return None

    def add_birthday(self, umo: str, name: str, month: int, day: int,
                     lunar: bool) -> dict:
        """新增一条生日记录，返回该条数据"""
        items = self._birthday_list(umo)
        idx = self._next_id(items)
        item = {
            "id": idx,
            "name": name,
            "month": month,
            "day": day,
            "lunar": bool(lunar),
            "time": self._fmt_time(self._now()),
            "last_trigger_year": None,   # 生日当天推送去重
            "last_announce_year": None,  # 提前预告推送去重
        }
        items.append(item)
        self._save_birthdays()
        return item

    def list_birthdays(self, umo: str) -> list[dict]:
        """查看指定会话全部生日记录"""
        return list(self._birthday_list(umo))

    def delete_birthday(self, umo: str, bid: int) -> bool:
        """按编号删除生日记录，删除成功返回 True"""
        items = self._birthday_list(umo)
        for i, item in enumerate(items):
            if item.get("id") == bid:
                items.pop(i)
                self._save_birthdays()
                return True
        return False

    def _birthday_this_year(self, item: dict, year: int) -> date | None:
        """计算生日在指定公历年份的日期；农历转换失败或日期非法返回 None"""
        month = _safe_int(item.get("month"), 0)
        day = _safe_int(item.get("day"), 0)
        if item.get("lunar"):
            return _lunar_to_solar(year, month, day)
        if 1 <= month <= 12 and 1 <= day <= 31:
            try:
                return date(year, month, day)
            except ValueError:
                return None
        return None

    def _birthday_advance_days(self) -> int:
        """生日提前预告天数，脏值回退默认 1（0 表示不预告）"""
        return max(0, _safe_int(self.config.get("birthday_advance_days", 1), 1))

    async def _check_birthdays(self, now: datetime) -> list:
        """检查到期生日并推送（当天祝福 + 提前预告），返回推送列表"""
        pushed = []
        year = now.year
        today = now.date()
        advance = self._birthday_advance_days()
        changed = False
        for umo, items in list(self._birthdays.items()):
            if not isinstance(items, list):
                continue
            for item in items:
                bdate = self._birthday_this_year(item, year)
                if bdate is None:
                    continue
                # 生日当天
                if bdate == today and item.get("last_trigger_year") != year:
                    text = f"🎂 今天是 {item.get('name', '')} 的生日，祝生日快乐！"
                    if await self._push(umo, text):
                        item["last_trigger_year"] = year
                        pushed.append((umo, item))
                        changed = True
                        continue
                # 提前预告
                if advance > 0 and item.get("last_announce_year") != year:
                    days_left = (bdate - today).days
                    if days_left == advance:
                        when = "明天" if advance == 1 else f"{advance} 天后"
                        text = f"📢 {when}是 {item.get('name', '')} 的生日，记得准备祝福哦！"
                        if await self._push(umo, text):
                            item["last_announce_year"] = year
                            pushed.append((umo, item))
                            changed = True
        if changed:
            self._save_birthdays()
        return pushed

    # ========== 倒计时 ==========

    def _countdown_list(self, umo: str) -> list[dict]:
        """获取指定会话的倒计时列表（确保存在）"""
        if umo not in self._countdowns or not isinstance(self._countdowns[umo], list):
            self._countdowns[umo] = []
        return self._countdowns[umo]

    def add_countdown(self, umo: str, name: str, target: date) -> dict:
        """新增一条倒计时，返回该条数据"""
        items = self._countdown_list(umo)
        idx = self._next_id(items)
        item = {
            "id": idx,
            "name": name,
            "target": target.isoformat(),
            "done": False,
            "last_push_date": None,
            "time": self._fmt_time(self._now()),
        }
        items.append(item)
        self._save_countdowns()
        return item

    def list_countdowns(self, umo: str) -> list[dict]:
        """查看指定会话全部倒计时"""
        return list(self._countdown_list(umo))

    def delete_countdown(self, umo: str, cid: int) -> bool:
        """按编号删除倒计时，删除成功返回 True"""
        items = self._countdown_list(umo)
        for i, item in enumerate(items):
            if item.get("id") == cid:
                items.pop(i)
                self._save_countdowns()
                return True
        return False

    def _countdown_push_time(self) -> tuple[int, int]:
        """倒计时每日播报时间，脏值回退默认 09:00"""
        raw = str(self.config.get("countdown_push_time", "09:00") or "09:00").strip()
        m = re.match(r"^(\d{1,2}):(\d{1,2})$", raw)
        if not m:
            return (9, 0)
        hour, minute = int(m.group(1)), int(m.group(2))
        if not _valid_time(hour, minute):
            return (9, 0)
        return (hour, minute)

    async def _check_countdowns(self, now: datetime) -> list:
        """每日到点播报倒计时剩余天数；到期当天标记完成，返回推送列表"""
        pushed = []
        hour, minute = self._countdown_push_time()
        if (now.hour, now.minute) < (hour, minute):
            return []
        today = now.date()
        today_s = today.isoformat()
        changed = False
        for umo, items in list(self._countdowns.items()):
            if not isinstance(items, list):
                continue
            for item in items:
                if item.get("last_push_date") == today_s:
                    continue
                target_s = str(item.get("target", ""))
                try:
                    target = date.fromisoformat(target_s)
                except ValueError:
                    continue
                name = item.get("name", "")
                if item.get("done"):
                    continue
                days = (target - today).days
                if days <= 0:
                    text = f"⏰ 倒计时到期：{name}（{target_s}）"
                    item["done"] = True
                else:
                    text = f"⏳ 距 {name} 还有 {days} 天"
                if await self._push(umo, text):
                    item["last_push_date"] = today_s
                    pushed.append((umo, item))
                    changed = True
        if changed:
            self._save_countdowns()
        return pushed

    # ========== 习惯打卡 ==========

    def _habit_list(self, umo: str) -> list[dict]:
        """获取指定会话的习惯列表（确保存在）"""
        if umo not in self._habits or not isinstance(self._habits[umo], list):
            self._habits[umo] = []
        return self._habits[umo]

    def add_habit(self, umo: str, name: str) -> dict:
        """新增一个习惯，返回该条数据"""
        items = self._habit_list(umo)
        idx = self._next_id(items)
        item = {
            "id": idx,
            "name": name,
            "total": 0,
            "streak": 0,
            "best": 0,
            "last_check": None,
            "last_remind_date": None,
            "time": self._fmt_time(self._now()),
        }
        items.append(item)
        self._save_habits()
        return item

    def check_habit(self, umo: str, name: str) -> dict:
        """按名称打卡，返回 (习惯条目, 是否今日首次打卡)；未找到返回 None"""
        items = self._habit_list(umo)
        today = self._now().date().isoformat()
        for item in items:
            if item.get("name") == name:
                if item.get("last_check") == today:
                    return (item, False)
                # 连续天数：昨天打过则 +1，否则重新计
                yesterday = (self._now().date() - timedelta(days=1)).isoformat()
                if item.get("last_check") == yesterday:
                    item["streak"] = _safe_int(item.get("streak"), 0) + 1
                else:
                    item["streak"] = 1
                item["total"] = _safe_int(item.get("total"), 0) + 1
                item["best"] = max(_safe_int(item.get("best"), 0), item["streak"])
                item["last_check"] = today
                self._save_habits()
                return (item, True)
        return None

    def delete_habit(self, umo: str, hid: int) -> bool:
        """按编号删除习惯，删除成功返回 True"""
        items = self._habit_list(umo)
        for i, item in enumerate(items):
            if item.get("id") == hid:
                items.pop(i)
                self._save_habits()
                return True
        return False

    def list_habits(self, umo: str) -> list[dict]:
        """查看指定会话全部习惯"""
        return list(self._habit_list(umo))

    def _habit_remind_time(self) -> tuple[int, int] | None:
        """每晚未打卡提醒时间，配置空/关返回 None"""
        enabled = self.config.get("habit_remind_enabled", True)
        if enabled is False or str(enabled).lower() in ("false", "0", "off", "no"):
            return None
        raw = str(self.config.get("habit_remind_time", "22:00") or "22:00").strip()
        m = re.match(r"^(\d{1,2}):(\d{1,2})$", raw)
        if not m:
            return (22, 0)
        hour, minute = int(m.group(1)), int(m.group(2))
        if not _valid_time(hour, minute):
            return (22, 0)
        return (hour, minute)

    async def _check_habits(self, now: datetime) -> list:
        """每晚到点提醒今天未打卡的习惯，返回推送列表"""
        pushed = []
        hm = self._habit_remind_time()
        if hm is None:
            return []
        hour, minute = hm
        if (now.hour, now.minute) < (hour, minute):
            return []
        today = now.date().isoformat()
        changed = False
        for umo, items in list(self._habits.items()):
            if not isinstance(items, list):
                continue
            pending = []
            for item in items:
                if item.get("last_check") != today and item.get("last_remind_date") != today:
                    pending.append(item)
            if not pending:
                continue
            lines = ["📌 今天还有未打卡的习惯："]
            for item in pending:
                lines.append(
                    f"#{item.get('id')} {item.get('name', '')}"
                    f"（已连续 {_safe_int(item.get('streak'), 0)} 天）"
                )
            lines.append("回复 /打卡 <名称> 完成打卡")
            if await self._push(umo, "\n".join(lines)):
                for item in pending:
                    item["last_remind_date"] = today
                pushed.append((umo, pending[0]))
                changed = True
        if changed:
            self._save_habits()
        return pushed

    # ========== 稍后提醒（自然语言） ==========

    @staticmethod
    def parse_later(text: str):
        """解析稍后提醒时间短语，返回目标时刻 (hour, minute) 或 None。

        支持：`5 分钟后`、`2小时后`、`明天 9 点`、`今晚 8 点`、
        `18:30`、`8点半`、`明天 18:30`。解析基准为调用时刻。
        """
        if not isinstance(text, str):
            return None
        text = text.strip()
        now = datetime.now()
        m = re.match(r"^(\d{1,3})\s*分钟(后)?$", text)
        if m:
            return (now + timedelta(minutes=int(m.group(1)))).strftime("%H:%M")
        m = re.match(r"^(\d{1,2})\s*小时(后)?$", text)
        if m:
            return (now + timedelta(hours=int(m.group(1)))).strftime("%H:%M")
        m = re.match(r"^明天\s*(\d{1,2})\s*[:点时]\s*(\d{1,2})?分?$", text)
        if m:
            hour = int(m.group(1))
            minute = int(m.group(2)) if m.group(2) else 0
            if _valid_time(hour, minute):
                return f"{hour:02d}:{minute:02d}"
            return None
        m = re.match(r"^(今晚|今天)\s*(\d{1,2})\s*[:点时]\s*(\d{1,2})?分?$", text)
        if m:
            hour = int(m.group(2))
            minute = int(m.group(3)) if m.group(3) else 0
            if not _valid_time(hour, minute):
                return None
            # 今晚 X 点：X < 12 时视为晚上（如 今晚 8 点 -> 20:00）
            if m.group(1) == "今晚" and hour < 12:
                hour += 12
            return f"{hour:02d}:{minute:02d}"
        m = re.match(r"^(\d{1,2})\s*[:点时]\s*(\d{1,2})?分?$", text)
        if m:
            hour = int(m.group(1))
            minute = int(m.group(2)) if m.group(2) else 0
            if _valid_time(hour, minute):
                return f"{hour:02d}:{minute:02d}"
            return None
        m = re.match(r"^(\d{1,2})点(半)?$", text)
        if m:
            hour = int(m.group(1))
            minute = 30 if m.group(2) else 0
            if _valid_time(hour, minute):
                return f"{hour:02d}:{minute:02d}"
        return None

    def parse_later_reminder(self, text: str):
        """解析稍后提醒指令文本：`<时间短语> <内容>`，返回 (hour, minute, content) 或 None"""
        if not isinstance(text, str):
            return None
        text = text.strip()
        m = re.match(r"^(\S+)\s+(.+)$", text)
        if not m:
            return None
        parsed = self.parse_later(m.group(1))
        if parsed is None:
            return None
        hour_s, minute_s = parsed.split(":")
        return (int(hour_s), int(minute_s), m.group(2).strip())

    # ========== 后台提醒循环 ==========

    async def initialize(self) -> None:
        """插件加载/重载时启动后台提醒任务"""
        await self._start_reminder_loop()

    @filter.on_astrbot_loaded()
    async def _start_reminder_loop(self):
        """启动后台提醒任务（幂等：重复调用不会重复启动）"""
        if self._reminder_running:
            return
        self._reminder_running = True
        self._reminder_task = asyncio.create_task(self._reminder_loop())

    async def _reminder_loop(self):
        """后台循环：每隔 check_interval 秒检查一次到期提醒并推送"""
        while self._reminder_running:
            try:
                await self._check_and_push()
            except Exception as e:  # noqa: BLE001
                logger.warning(f"【{PLUGIN_NAME}】提醒检查异常: {e}")
            try:
                await asyncio.sleep(self._check_interval())
            except asyncio.CancelledError:
                raise

    async def _check_and_push(self):
        """检查所有会话的到期提醒并推送（一次性触发后删除，周期提醒记录触发日期）"""
        now = self._now()
        pushed = []
        for umo, items in list(self._reminders.items()):
            if not isinstance(items, list):
                continue
            remain = []
            for rem in items:
                if not self._is_due(rem, now):
                    remain.append(rem)
                    continue
                # 触发推送；推送失败则不记账（下一轮重试）
                rtype = rem.get("type")
                text = self._render_reminder(rem)
                if not await self._push(umo, text):
                    remain.append(rem)
                    continue
                pushed.append((umo, rem))
                if rtype == TYPE_ONCE:
                    # 一次性提醒触发后即删除
                    continue
                if rtype == TYPE_DAILY:
                    rem["last_trigger_date"] = now.strftime("%Y-%m-%d")
                    remain.append(rem)
                elif rtype == TYPE_WEEKLY:
                    rem["last_trigger_week"] = self._week_key(now)
                    remain.append(rem)
            self._reminders[umo] = remain
        if pushed:
            self._save_reminders()
        # 生日 / 倒计时 / 习惯打卡检查
        pushed.extend(await self._check_birthdays(now))
        pushed.extend(await self._check_countdowns(now))
        pushed.extend(await self._check_habits(now))
        return pushed

    def _render_reminder(self, rem: dict) -> str:
        """生成提醒推送文案"""
        rtype = rem.get("type")
        hour = _safe_int(rem.get("hour"), 0)
        minute = _safe_int(rem.get("minute"), 0)
        if rtype == TYPE_ONCE:
            return f"⏰ 定时提醒（一次性）：{rem.get('content', '')}"
        if rtype == TYPE_DAILY:
            return f"⏰ 每日提醒 {hour:02d}:{minute:02d}：{rem.get('content', '')}"
        if rtype == TYPE_WEEKLY:
            wd = _safe_int(rem.get("weekday"), 0)
            names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
            wname = names[wd] if 0 <= wd < 7 else str(wd)
            return f"⏰ 每周{wname}提醒 {hour:02d}:{minute:02d}：{rem.get('content', '')}"
        return f"⏰ 提醒：{rem.get('content', '')}"

    async def _push(self, umo: str, text: str) -> bool:
        """推送消息到指定会话；成功返回 True（context 缺失或失败返回 False，便于调用方重试）"""
        if not self.context:
            return False
        try:
            await self.context.send_message(umo, self._chain(text))
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning(f"【{PLUGIN_NAME}】推送消息到 {umo} 失败: {e}")
            return False

    # ========== 指令处理 ==========

    @filter.command("备忘", priority=200)
    async def memo_cmd(self, event: AstrMessageEvent):
        """处理 /备忘 系列指令：新增、列表、删除"""
        umo = str(event.session)
        text = event.message_str.strip()
        rest = text[len("备忘"):].strip()

        if not rest:
            return self._reply(
                event,
                "使用方法：\n"
                "/备忘 <内容>      记录一条备忘\n"
                "/备忘 列表         查看当前会话备忘\n"
                "/备忘 删 <编号>    删除指定编号备忘",
            )
        if rest == "列表":
            return self._reply(event, self._render_memo_list(umo))
        m = re.match(r"^删\s*(\d+)\s*$", rest)
        if m:
            mid = int(m.group(1))
            if self.delete_memo(umo, mid):
                return self._reply(event, f"已删除备忘 #{mid}")
            return self._reply(event, f"未找到备忘 #{mid}")
        # 新增备忘
        item = self.add_memo(umo, rest)
        return self._reply(event, f"已记录备忘 #{item['id']}：{item['content']}")

    def _render_memo_list(self, umo: str) -> str:
        """渲染当前会话备忘列表文案"""
        items = self.list_memos(umo)
        if not items:
            return "当前会话暂无备忘。发送 /备忘 <内容> 添加。"
        lines = ["当前会话备忘："]
        for it in items:
            lines.append(f"#{it['id']} {it['content']}（{it.get('time', '')}）")
        return "\n".join(lines)

    @filter.command("提醒", priority=200)
    async def reminder_cmd(self, event: AstrMessageEvent):
        """处理 /提醒 系列指令：新增（一次性/周期）、列表、删除"""
        umo = str(event.session)
        text = event.message_str.strip()
        rest = text[len("提醒"):].strip()

        if not rest:
            return self._reply(
                event,
                "使用方法：\n"
                "/提醒 HH:MM <内容>          一次性提醒\n"
                "/提醒 每天 HH:MM <内容>      每日提醒\n"
                "/提醒 每周 HH:MM <内容>      每周提醒\n"
                "/提醒 列表                   查看当前会话提醒\n"
                "/提醒 删 <编号>              删除指定编号提醒",
            )
        if rest == "列表":
            return self._reply(event, self._render_reminder_list(umo))
        m = re.match(r"^删\s*(\d+)\s*$", rest)
        if m:
            rid = int(m.group(1))
            if self.delete_reminder(umo, rid):
                return self._reply(event, f"已删除提醒 #{rid}")
            return self._reply(event, f"未找到提醒 #{rid}")
        # 解析新增
        parsed = self.parse_reminder(rest)
        if parsed is None:
            return self._reply(
                event,
                "格式无法解析。示例：\n"
                "/提醒 18:30 下班打卡\n"
                "/提醒 每天 09:00 晨会\n"
                "/提醒 每周 18:00 周报提交",
            )
        rtype, hour, minute, _weekday, content = parsed
        rem = self.add_reminder(umo, rtype, hour, minute, _weekday, content)
        if rem is None:
            return self._reply(event, f"当前会话提醒数量已达上限（{self._max_reminders()}），请先删除部分提醒。")
        label = self._reminder_label(rem)
        return self._reply(event, f"已设置{label}提醒 #{rem['id']}：{rem['content']}")

    def _reminder_label(self, rem: dict) -> str:
        """根据提醒类型生成中文标签"""
        rtype = rem.get("type")
        hh = f"{_safe_int(rem.get('hour'), 0):02d}:{_safe_int(rem.get('minute'), 0):02d}"
        if rtype == TYPE_ONCE:
            return f"一次性（{hh}）"
        if rtype == TYPE_DAILY:
            return f"每日（{hh}）"
        if rtype == TYPE_WEEKLY:
            wd = _safe_int(rem.get("weekday"), 0)
            names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
            wname = names[wd] if 0 <= wd < 7 else str(wd)
            return f"每周{wname}（{hh}）"
        return ""

    def _render_reminder_list(self, umo: str) -> str:
        """渲染当前会话提醒列表文案"""
        items = self.list_reminders(umo)
        if not items:
            return "当前会话暂无提醒。发送 /提醒 HH:MM <内容> 添加。"
        lines = ["当前会话提醒："]
        for r in items:
            lines.append(f"#{r['id']} {self._reminder_label(r)} {r['content']}")
        return "\n".join(lines)

    # ========== 生日指令 ==========

    @filter.command("生日", priority=200)
    async def birthday_cmd(self, event: AstrMessageEvent):
        """处理 /生日 系列指令：新增、列表、删除"""
        umo = str(event.session)
        text = event.message_str.strip()
        rest = text[len("生日"):].strip()

        if not rest:
            return self._reply(
                event,
                "使用方法：\n"
                "/生日 <名字> YYYY-MM-DD      记录公历生日\n"
                "/生日 <名字> 8月18日         记录公历生日\n"
                "/生日 <名字> 农历 八月十五    记录农历生日\n"
                "/生日 列表                   查看当前会话生日\n"
                "/生日 删 <编号>              删除指定编号",
            )
        if rest == "列表":
            return self._reply(event, self._render_birthday_list(umo))
        m = re.match(r"^删\s*(\d+)\s*$", rest)
        if m:
            bid = int(m.group(1))
            if self.delete_birthday(umo, bid):
                return self._reply(event, f"已删除生日记录 #{bid}")
            return self._reply(event, f"未找到生日记录 #{bid}")
        parsed = self.parse_birthday(rest)
        if parsed is None:
            return self._reply(
                event,
                "格式无法解析。示例：\n"
                "/生日 小明 2026-08-18\n"
                "/生日 小红 农历 八月十五",
            )
        name, month, day, lunar = parsed
        item = self.add_birthday(umo, name, month, day, lunar)
        if lunar:
            label = f"农历{_lunar_month_name(month)}{day}日"
        else:
            label = f"{month}月{day}日"
        return self._reply(event, f"已记录 {name} 的生日（{label}，每年自动提醒）")

    def _render_birthday_list(self, umo: str) -> str:
        """渲染当前会话生日列表文案"""
        items = self.list_birthdays(umo)
        if not items:
            return "当前会话暂无生日记录。发送 /生日 <名字> YYYY-MM-DD 添加。"
        lines = ["当前会话生日记录："]
        for it in items:
            if it.get("lunar"):
                label = f"农历{_lunar_month_name(_safe_int(it.get('month'), 1))}{_safe_int(it.get('day'), 1)}日"
            else:
                label = f"{_safe_int(it.get('month'), 1)}月{_safe_int(it.get('day'), 1)}日"
            lines.append(f"#{it['id']} {it.get('name', '')}（{label}）")
        return "\n".join(lines)

    # ========== 倒计时指令 ==========

    @filter.command("倒计时", priority=200)
    async def countdown_cmd(self, event: AstrMessageEvent):
        """处理 /倒计时 系列指令：新增、列表、删除"""
        umo = str(event.session)
        text = event.message_str.strip()
        rest = text[len("倒计时"):].strip()

        if not rest:
            return self._reply(
                event,
                "使用方法：\n"
                "/倒计时 <名称> YYYY-MM-DD   创建倒计时\n"
                "/倒计时 列表                 查看当前会话倒计时\n"
                "/倒计时 删 <编号>            删除指定编号",
            )
        if rest == "列表":
            return self._reply(event, self._render_countdown_list(umo))
        m = re.match(r"^删\s*(\d+)\s*$", rest)
        if m:
            cid = int(m.group(1))
            if self.delete_countdown(umo, cid):
                return self._reply(event, f"已删除倒计时 #{cid}")
            return self._reply(event, f"未找到倒计时 #{cid}")
        m = re.match(r"^(\S+)\s+(\d{4})-(\d{1,2})-(\d{1,2})\s*$", rest)
        if not m:
            return self._reply(
                event,
                "格式无法解析。示例：\n/倒计时 项目上线 2026-12-31",
            )
        name = m.group(1)
        try:
            target = date(int(m.group(2)), int(m.group(3)), int(m.group(4)))
        except ValueError:
            return self._reply(event, "日期无效，请检查格式（YYYY-MM-DD）。")
        item = self.add_countdown(umo, name, target)
        days = (target - self._now().date()).days
        if days < 0:
            return self._reply(event, f"已创建倒计时 #{item['id']}：{name}（目标 {target}，已过期 {abs(days)} 天）")
        return self._reply(event, f"已创建倒计时 #{item['id']}：{name}（距目标还有 {days} 天）")

    def _render_countdown_list(self, umo: str) -> str:
        """渲染当前会话倒计时列表文案"""
        items = self.list_countdowns(umo)
        if not items:
            return "当前会话暂无倒计时。发送 /倒计时 <名称> YYYY-MM-DD 添加。"
        today = self._now().date()
        lines = ["当前会话倒计时："]
        for it in items:
            try:
                target = date.fromisoformat(str(it.get("target", "")))
            except ValueError:
                continue
            if it.get("done"):
                lines.append(f"#{it['id']} {it.get('name', '')}（已到期）")
            else:
                days = (target - today).days
                lines.append(f"#{it['id']} {it.get('name', '')}（还剩 {max(days, 0)} 天，{target}）")
        return "\n".join(lines)

    # ========== 习惯打卡指令 ==========

    @filter.command("打卡", priority=200)
    async def habit_cmd(self, event: AstrMessageEvent):
        """处理 /打卡 系列指令：新增、打卡、列表、删除"""
        umo = str(event.session)
        text = event.message_str.strip()
        rest = text[len("打卡"):].strip()

        if not rest:
            return self._reply(
                event,
                "使用方法：\n"
                "/打卡 加 <名称>     创建习惯\n"
                "/打卡 <名称>        完成今日打卡\n"
                "/打卡 列表          查看当前会话习惯\n"
                "/打卡 删 <编号>     删除指定习惯",
            )
        if rest == "列表":
            return self._reply(event, self._render_habit_list(umo))
        m = re.match(r"^删\s*(\d+)\s*$", rest)
        if m:
            hid = int(m.group(1))
            if self.delete_habit(umo, hid):
                return self._reply(event, f"已删除习惯 #{hid}")
            return self._reply(event, f"未找到习惯 #{hid}")
        m = re.match(r"^加\s+(.+)$", rest)
        if m:
            item = self.add_habit(umo, m.group(1).strip())
            return self._reply(event, f"已创建习惯 #{item['id']}：{item['name']}，回复 /打卡 {item['name']} 完成打卡")
        result = self.check_habit(umo, rest)
        if result is None:
            return self._reply(event, f"未找到习惯「{rest}」。可用 /打卡 加 <名称> 创建。")
        item, first = result
        if not first:
            return self._reply(event, f"「{item['name']}」今天已打卡，明天再来吧！")
        return self._reply(
            event,
            f"✅ 「{item['name']}」打卡成功！"
            f"已连续 {item['streak']} 天，累计 {item['total']} 次，最佳 {item['best']} 天。",
        )

    def _render_habit_list(self, umo: str) -> str:
        """渲染当前会话习惯列表文案"""
        items = self.list_habits(umo)
        if not items:
            return "当前会话暂无习惯。发送 /打卡 加 <名称> 创建。"
        today = self._now().date().isoformat()
        lines = ["当前会话习惯："]
        for it in items:
            status = "✅今日已打卡" if it.get("last_check") == today else "⬜今日未打卡"
            lines.append(
                f"#{it['id']} {it.get('name', '')}（连续 {_safe_int(it.get('streak'), 0)} 天，"
                f"累计 {_safe_int(it.get('total'), 0)} 次，最佳 {_safe_int(it.get('best'), 0)} 天）{status}"
            )
        return "\n".join(lines)

    # ========== 稍后提醒指令 ==========

    @filter.command("稍后", priority=200)
    async def later_cmd(self, event: AstrMessageEvent):
        """处理 /稍后 指令：自然语言临时提醒，如 `/稍后 30分钟后 取快递`"""
        umo = str(event.session)
        text = event.message_str.strip()
        rest = text[len("稍后"):].strip()
        parsed = self.parse_later_reminder(rest)
        if parsed is None:
            return self._reply(
                event,
                "使用方法：/稍后 <时间短语> <内容>\n"
                "时间短语示例：5 分钟后 / 2小时后 / 明天 9 点 / 今晚 8 点 / 18:30 / 8点半",
            )
        hour, minute, content = parsed
        rem = self.add_reminder(umo, TYPE_ONCE, hour, minute, None, content)
        if rem is None:
            return self._reply(event, f"当前会话提醒数量已达上限（{self._max_reminders()}），请先删除部分提醒。")
        return self._reply(
            event,
            f"⏰ 已设置稍后提醒 #{rem['id']}：{content}（{hour:02d}:{minute:02d} 触发）",
        )

    async def terminate(self):
        """插件卸载时安全取消后台任务"""
        self._reminder_running = False
        if self._reminder_task:
            self._reminder_task.cancel()
            try:
                await self._reminder_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass