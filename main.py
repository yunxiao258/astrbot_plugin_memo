# -*- coding: utf-8 -*-
"""AstrBot 群聊备忘录与定时提醒插件：记录备忘、一次性/周期定时提醒"""

import asyncio
import json
import os
import re
from datetime import datetime, timedelta

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.message_components import Plain
from astrbot.api.star import Context, Star, register
from astrbot.api.all import MessageChain

# 插件元数据
PLUGIN_NAME = "astrbot_plugin_memo"
PLUGIN_AUTHOR = "Administrator"
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
        self._load_memos()
        self._load_reminders()

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

    def _save_json(self, path, data):
        """保存 JSON 文件（写入失败不影响内存数据）"""
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
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
        """构造纯文本消息链"""
        return MessageChain([Plain(text)])

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

    # ========== 后台提醒循环 ==========

    @filter.on_astrbot_loaded()
    async def _start_reminder_loop(self):
        """AstrBot 加载完成后启动后台提醒任务（可安全取消）"""
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
                # 触发推送
                rtype = rem.get("type")
                text = self._render_reminder(rem)
                await self._push(umo, text)
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
        return pushed

    def _render_reminder(self, rem: dict) -> str:
        """生成提醒推送文案"""
        rtype = rem.get("type")
        if rtype == TYPE_ONCE:
            return f"⏰ 定时提醒（一次性）：{rem.get('content', '')}"
        if rtype == TYPE_DAILY:
            return f"⏰ 每日提醒 {rem.get('hour', 0):02d}:{rem.get('minute', 0):02d}：{rem.get('content', '')}"
        if rtype == TYPE_WEEKLY:
            wd = _safe_int(rem.get("weekday"), 0)
            names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
            wname = names[wd] if 0 <= wd < 7 else str(wd)
            return f"⏰ 每周{wname}提醒 {rem.get('hour', 0):02d}:{rem.get('minute', 0):02d}：{rem.get('content', '')}"
        return f"⏰ 提醒：{rem.get('content', '')}"

    async def _push(self, umo: str, text: str):
        """推送消息到指定会话（context 缺失时静默跳过，避免测试/异常崩溃）"""
        if not self.context:
            return
        try:
            await self.context.send_message(umo, self._chain(text))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"【{PLUGIN_NAME}】推送消息到 {umo} 失败: {e}")

    # ========== 指令处理 ==========

    @filter.command("备忘", priority=200)
    async def memo_cmd(self, event: AstrMessageEvent):
        """处理 /备忘 系列指令：新增、列表、删除"""
        umo = str(event.session)
        text = event.message_str.strip()
        rest = text[len("备忘"):].strip()

        if not rest:
            return self._chain(
                "使用方法：\n"
                "/备忘 <内容>      记录一条备忘\n"
                "/备忘 列表         查看当前会话备忘\n"
                "/备忘 删 <编号>    删除指定编号备忘"
            )
        if rest == "列表":
            return self._chain(self._render_memo_list(umo))
        m = re.match(r"^删\s*(\d+)\s*$", rest)
        if m:
            mid = int(m.group(1))
            if self.delete_memo(umo, mid):
                return self._chain(f"已删除备忘 #{mid}")
            return self._chain(f"未找到备忘 #{mid}")
        # 新增备忘
        item = self.add_memo(umo, rest)
        return self._chain(f"已记录备忘 #{item['id']}：{item['content']}")

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
            return self._chain(
                "使用方法：\n"
                "/提醒 HH:MM <内容>          一次性提醒\n"
                "/提醒 每天 HH:MM <内容>      每日提醒\n"
                "/提醒 每周 HH:MM <内容>      每周提醒\n"
                "/提醒 列表                   查看当前会话提醒\n"
                "/提醒 删 <编号>              删除指定编号提醒"
            )
        if rest == "列表":
            return self._chain(self._render_reminder_list(umo))
        m = re.match(r"^删\s*(\d+)\s*$", rest)
        if m:
            rid = int(m.group(1))
            if self.delete_reminder(umo, rid):
                return self._chain(f"已删除提醒 #{rid}")
            return self._chain(f"未找到提醒 #{rid}")
        # 解析新增
        parsed = self.parse_reminder(rest)
        if parsed is None:
            return self._chain(
                "格式无法解析。示例：\n"
                "/提醒 18:30 下班打卡\n"
                "/提醒 每天 09:00 晨会\n"
                "/提醒 每周 18:00 周报提交"
            )
        rtype, hour, minute, _weekday, content = parsed
        rem = self.add_reminder(umo, rtype, hour, minute, _weekday, content)
        if rem is None:
            return self._chain(f"当前会话提醒数量已达上限（{self._max_reminders()}），请先删除部分提醒。")
        label = self._reminder_label(rem)
        return self._chain(f"已设置{label}提醒 #{rem['id']}：{rem['content']}")

    def _reminder_label(self, rem: dict) -> str:
        """根据提醒类型生成中文标签"""
        rtype = rem.get("type")
        hh = f"{rem.get('hour', 0):02d}:{rem.get('minute', 0):02d}"
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

    async def terminate(self):
        """插件卸载时安全取消后台任务"""
        self._reminder_running = False
        if self._reminder_task:
            self._reminder_task.cancel()
            try:
                await self._reminder_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass