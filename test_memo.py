# -*- coding: utf-8 -*-
"""astrbot_plugin_memo 单元测试：备忘增删查、提醒解析、到期判断与去重、后台循环推送"""
import sys
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

sys.path.insert(0, r"D:\astrbot\data\plugins")

from astrbot_plugin_memo.main import (  # noqa: E402
    MemoPlugin,
    TYPE_ONCE,
    TYPE_DAILY,
    TYPE_WEEKLY,
)


class MemStore:
    """内存持久化替身：替代真实 plugin_data 文件的读写"""

    def __init__(self):
        self.data = {}

    def load(self, path, default):
        return self.data.get(path, default)

    def save(self, path, data):
        self.data[path] = data


class FakeContext:
    """最小 context 替身：仅支持 send_message"""

    def __init__(self):
        self.sent = []  # [(umo, chain)]

    async def send_message(self, umo, chain):
        self.sent.append((umo, chain))


class MemoPluginTestBase(unittest.TestCase):
    """公共基类：使用内存持久化与假 context，不触碰真实 plugin_data"""

    def setUp(self):
        self.store = MemStore()
        # 屏蔽文件读写与目录创建，全部走内存
        self._load_p = patch.object(MemoPlugin, "_load_json",
                                    side_effect=lambda path, dt: self.store.load(path, {}))
        self._save_p = patch.object(MemoPlugin, "_save_json",
                                    side_effect=lambda path, data: self.store.save(path, data))
        self._mkdir_p = patch("os.makedirs")
        self._load_p.start()
        self._save_p.start()
        self._mkdir_p.start()
        self.addCleanup(self._load_p.stop)
        self.addCleanup(self._save_p.stop)
        self.addCleanup(self._mkdir_p.stop)

        self.ctx = FakeContext()
        self.p = MemoPlugin(self.ctx, {"check_interval": 1})

    def set_now(self, dt: datetime):
        """覆盖 _now，模拟当前时间"""
        self.p._now = lambda: dt

    @staticmethod
    def _run(coro):
        """运行一个协程（供测试调用）"""
        import asyncio

        return asyncio.run(coro)


class TestMemoCrud(MemoPluginTestBase):
    """备忘：新增、查看、删除"""

    UMO = "default:GroupMessage:123"

    def test_add_memo(self):
        item = self.p.add_memo(self.UMO, "买菜")
        self.assertEqual(item["content"], "买菜")
        self.assertGreaterEqual(item["id"], 1)
        self.assertTrue(item["time"])

    def test_list_memos(self):
        self.p.add_memo(self.UMO, "买菜")
        self.p.add_memo(self.UMO, "取快递")
        items = self.p.list_memos(self.UMO)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["id"], 1)
        self.assertEqual(items[1]["id"], 2)
        # 编号递增
        self.assertEqual([i["id"] for i in items], [1, 2])

    def test_delete_memo(self):
        self.p.add_memo(self.UMO, "买菜")
        self.assertTrue(self.p.delete_memo(self.UMO, 1))
        self.assertEqual(self.p.list_memos(self.UMO), [])
        # 删除不存在的编号
        self.assertFalse(self.p.delete_memo(self.UMO, 99))

    def test_memo_session_isolated(self):
        # 群聊与私聊各自独立
        a = "default:GroupMessage:123"
        b = "default:PrivateMessage:456"
        self.p.add_memo(a, "群备忘")
        self.p.add_memo(b, "私聊备忘")
        self.assertEqual(len(self.p.list_memos(a)), 1)
        self.assertEqual(len(self.p.list_memos(b)), 1)
        self.assertEqual(self.p.list_memos(a)[0]["content"], "群备忘")

    def test_memo_cap(self):
        # 超过上限自动丢弃最旧
        self.p.config["max_memos"] = 2
        self.p.add_memo(self.UMO, "a")
        self.p.add_memo(self.UMO, "b")
        self.p.add_memo(self.UMO, "c")
        items = self.p.list_memos(self.UMO)
        self.assertEqual(len(items), 2)
        self.assertEqual([i["content"] for i in items], ["b", "c"])


class TestParseReminder(MemoPluginTestBase):
    """提醒解析：一次性 / 每天 / 每周"""

    def test_once(self):
        r = self.p.parse_reminder("18:30 下班")
        self.assertIsNotNone(r)
        self.assertEqual(r[0], TYPE_ONCE)
        self.assertEqual(r[1], 18)
        self.assertEqual(r[2], 30)
        self.assertEqual(r[4], "下班")

    def test_daily(self):
        r = self.p.parse_reminder("每天 09:00 晨会")
        self.assertEqual(r[0], TYPE_DAILY)
        self.assertEqual((r[1], r[2]), (9, 0))
        self.assertEqual(r[4], "晨会")

    def test_weekly(self):
        r = self.p.parse_reminder("每周 18:00 周报")
        self.assertEqual(r[0], TYPE_WEEKLY)
        self.assertEqual((r[1], r[2]), (18, 0))
        self.assertEqual(r[4], "周报")

    def test_invalid_time(self):
        # 小时越界 / 分钟越界
        self.assertIsNone(self.p.parse_reminder("25:00 内容"))
        self.assertIsNone(self.p.parse_reminder("12:99 内容"))
        self.assertIsNone(self.p.parse_reminder("每天 24:00 内容"))

    def test_missing_content(self):
        self.assertIsNone(self.p.parse_reminder("12:00"))
        self.assertIsNone(self.p.parse_reminder("每天 12:00"))

    def test_invalid_input_type(self):
        self.assertIsNone(self.p.parse_reminder(None))
        self.assertIsNone(self.p.parse_reminder(12345))

    def test_trim_content(self):
        r = self.p.parse_reminder("  08:30   打卡  ")
        self.assertEqual(r[4], "打卡")


class TestDueOnce(MemoPluginTestBase):
    """一次性提醒到期判断与去重"""

    UMO = "default:GroupMessage:123"

    def test_once_due_when_now_passes(self):
        base = datetime(2026, 8, 14, 8, 0, 0)
        self.set_now(base)
        self.p.add_reminder(self.UMO, TYPE_ONCE, 18, 30, None, "下班")
        rem = self.p.list_reminders(self.UMO)[0]
        # 未到时间不到期
        self.set_now(datetime(2026, 8, 14, 18, 0, 0))
        self.assertFalse(self.p._is_due(rem, datetime(2026, 8, 14, 18, 0, 0)))
        # 到点/过后到期
        self.set_now(datetime(2026, 8, 14, 18, 30, 0))
        self.assertTrue(self.p._is_due(rem, datetime(2026, 8, 14, 18, 30, 0)))

    def test_once_next_tomorrow_when_past(self):
        base = datetime(2026, 8, 14, 19, 0, 0)
        # 今天 18:30 已过，下一次是明天
        nxt = MemoPlugin._once_next(18, 30, base)
        self.assertEqual((nxt.day, nxt.hour, nxt.minute), (15, 18, 30))


class TestDueDaily(MemoPluginTestBase):
    """每天提醒到期判断与按日期去重"""

    UMO = "default:GroupMessage:123"

    def test_daily_due_and_dedup(self):
        self.set_now(datetime(2026, 8, 14, 6, 0, 0))
        self.p.add_reminder(self.UMO, TYPE_DAILY, 9, 0, None, "晨会")
        rem = self.p.list_reminders(self.UMO)[0]
        # 未到 09:00 不到期
        self.assertFalse(self.p._is_due(rem, datetime(2026, 8, 14, 8, 0, 0)))
        # 到 09:00 到期
        self.assertTrue(self.p._is_due(rem, datetime(2026, 8, 14, 9, 0, 0)))
        # 触发后同一天不再到期（去重）
        rem["last_trigger_date"] = "2026-08-14"
        self.assertFalse(self.p._is_due(rem, datetime(2026, 8, 14, 12, 0, 0)))
        # 次日再次到期
        self.assertTrue(self.p._is_due(rem, datetime(2026, 8, 15, 9, 0, 0)))


class TestDueWeekly(MemoPluginTestBase):
    """每周提醒到期判断与按周去重"""

    UMO = "default:GroupMessage:123"

    def test_weekly_weekday_assigned(self):
        # 在周三创建 -> weekday 应为 2
        self.set_now(datetime(2026, 8, 12, 10, 0, 0))  # 2026-08-12 是周三
        self.p.add_reminder(self.UMO, TYPE_WEEKLY, 18, 0, None, "周报")
        rem = self.p.list_reminders(self.UMO)[0]
        self.assertEqual(rem["weekday"], 2)

    def test_weekly_due_and_dedup(self):
        rem = {
            "id": 1, "type": TYPE_WEEKLY, "hour": 18, "minute": 0,
            "weekday": 2, "content": "周报", "last_trigger_week": None,
        }
        # 周三 18:00 到期（2026-08-12 是周三）
        due_now = datetime(2026, 8, 12, 18, 0, 0)
        self.assertTrue(self.p._is_due(rem, due_now))
        # 非周三不到期
        self.assertFalse(self.p._is_due(rem, datetime(2026, 8, 13, 18, 0, 0)))
        # 同周触发后去重（本周一为 2026-08-10）
        rem["last_trigger_week"] = self.p._week_key(due_now)
        self.assertFalse(self.p._is_due(rem, datetime(2026, 8, 14, 18, 0, 0)))
        # 下周再次到期（本周一 2026-08-17）
        self.assertTrue(self.p._is_due(rem, datetime(2026, 8, 19, 18, 0, 0)))


class TestBackgroundPush(MemoPluginTestBase):
    """后台循环触发推送"""

    UMO = "default:GroupMessage:123"

    def test_check_push_once_removes(self):
        base = datetime(2026, 8, 14, 6, 0, 0)
        self.set_now(base)
        self.p.add_reminder(self.UMO, TYPE_ONCE, 18, 30, None, "下班")
        # 到点
        self.set_now(datetime(2026, 8, 14, 18, 30, 0))
        pushed = self._run(self.p._check_and_push())
        self.assertEqual(len(pushed), 1)
        self.assertEqual(pushed[0][0], self.UMO)
        # 一次性触发后已删除
        self.assertEqual(self.p.list_reminders(self.UMO), [])
        # 已推送到正确会话，文案包含内容
        self.assertEqual(len(self.ctx.sent), 1)
        self.assertEqual(self.ctx.sent[0][0], self.UMO)
        self.assertIn("下班", self.ctx.sent[0][1].__str__())

    def test_check_push_daily_dedup_keeps(self):
        self.set_now(datetime(2026, 8, 14, 6, 0, 0))
        self.p.add_reminder(self.UMO, TYPE_DAILY, 9, 0, None, "晨会")
        self.set_now(datetime(2026, 8, 14, 9, 0, 0))
        pushed = self._run(self.p._check_and_push())
        self.assertEqual(len(pushed), 1)
        # 每日提醒保留，仅记录触发日期
        rem = self.p.list_reminders(self.UMO)[0]
        self.assertEqual(rem["last_trigger_date"], "2026-08-14")
        # 再次检查同日不再推送
        pushed2 = self._run(self.p._check_and_push())
        self.assertEqual(len(pushed2), 0)

    def test_loop_start_and_terminate(self):
        # 验证后台循环可安全启动并取消
        self.p.config["check_interval"] = 60
        self._run(self.p._start_reminder_loop())
        self.assertTrue(self.p._reminder_running)
        self._run(self.p.terminate())
        self.assertFalse(self.p._reminder_running)


if __name__ == "__main__":
    unittest.main(verbosity=2)