# -*- coding: utf-8 -*-
"""astrbot_plugin_memo 新功能测试：生日（含农历）、倒计时、习惯打卡、稍后提醒"""
import sys
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

sys.path.insert(0, r"D:\astrbot\data\plugins")

from astrbot_plugin_memo.main import (  # noqa: E402
    MemoPlugin,
    _lunar_to_solar,
    _solar_to_lunar,
    _lunar_month_name,
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


class MemoExtraTestBase(unittest.TestCase):
    """公共基类：使用内存持久化与假 context，不触碰真实 plugin_data"""

    def setUp(self):
        self.store = MemStore()
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


class TestLunar(MemoExtraTestBase):
    """农历公历互转"""

    def test_solar_to_lunar_known(self):
        # 已知春节日期
        self.assertEqual(_solar_to_lunar(2026, 2, 17), (2026, 1, 1, False))
        self.assertEqual(_solar_to_lunar(2025, 1, 29), (2025, 1, 1, False))
        self.assertEqual(_solar_to_lunar(2024, 2, 10), (2024, 1, 1, False))
        # 中秋
        self.assertEqual(_solar_to_lunar(2026, 9, 25), (2026, 8, 15, False))

    def test_lunar_to_solar_known(self):
        self.assertEqual(_lunar_to_solar(2026, 1, 1), __import__("datetime").date(2026, 2, 17))
        self.assertEqual(_lunar_to_solar(2026, 8, 15), __import__("datetime").date(2026, 9, 25))

    def test_round_trip(self):
        from datetime import date
        import random

        random.seed(1)
        for _ in range(50):
            d = date(1900, 1, 31) + timedelta(days=random.randint(0, 73000))
            r = _solar_to_lunar(d.year, d.month, d.day)
            self.assertIsNotNone(r)
            ly, lm, ld, leap = r
            self.assertEqual(_lunar_to_solar(ly, lm, ld, leap), d)

    def test_invalid_input(self):
        self.assertIsNone(_lunar_to_solar(1899, 1, 1))
        self.assertIsNone(_lunar_to_solar(2026, 13, 1))
        self.assertIsNone(_lunar_to_solar(2026, 1, 40))
        self.assertIsNone(_solar_to_lunar(1899, 12, 31))
        self.assertIsNone(_solar_to_lunar(2026, 2, 30))

    def test_month_name(self):
        self.assertEqual(_lunar_month_name(1), "正月")
        self.assertEqual(_lunar_month_name(12), "腊月")


class TestBirthday(MemoExtraTestBase):
    """生日：解析、增删查、到期推送与去重"""

    UMO = "default:GroupMessage:123"

    def test_parse_birthday_solar(self):
        r = self.p.parse_birthday("小明 2026-08-18")
        self.assertEqual(r, ("小明", 8, 18, False))
        r2 = self.p.parse_birthday("小红 3月5日")
        self.assertEqual(r2, ("小红", 3, 5, False))

    def test_parse_birthday_lunar(self):
        r = self.p.parse_birthday("小明 农历 八月十五")
        self.assertEqual(r, ("小明", 8, 15, True))

    def test_parse_birthday_invalid(self):
        self.assertIsNone(self.p.parse_birthday("小明 2026-13-40"))
        self.assertIsNone(self.p.parse_birthday("小明 农历 13月1日"))
        self.assertIsNone(self.p.parse_birthday("小明"))
        self.assertIsNone(self.p.parse_birthday(None))

    def test_crud(self):
        self.p.add_birthday(self.UMO, "小明", 8, 18, False)
        self.p.add_birthday(self.UMO, "小红", 8, 15, True)
        items = self.p.list_birthdays(self.UMO)
        self.assertEqual(len(items), 2)
        self.assertTrue(self.p.delete_birthday(self.UMO, 1))
        self.assertFalse(self.p.delete_birthday(self.UMO, 99))
        self.assertEqual(len(self.p.list_birthdays(self.UMO)), 1)

    def test_solar_push_on_birthday(self):
        self.set_now(datetime(2026, 8, 17, 8, 0, 0))
        self.p.add_birthday(self.UMO, "小明", 8, 18, False)
        # 生日当天推送
        self.set_now(datetime(2026, 8, 18, 9, 0, 0))
        pushed = self._run(self.p._check_birthdays(datetime(2026, 8, 18, 9, 0, 0)))
        self.assertEqual(len(pushed), 1)
        self.assertIn("生日", self.ctx.sent[0][1].__str__())
        # 同日不重复推送
        pushed2 = self._run(self.p._check_birthdays(datetime(2026, 8, 18, 12, 0, 0)))
        self.assertEqual(len(pushed2), 0)

    def test_advance_announce(self):
        self.set_now(datetime(2026, 8, 17, 8, 0, 0))
        self.p.add_birthday(self.UMO, "小明", 8, 18, False)
        # 提前 1 天预告
        pushed = self._run(self.p._check_birthdays(datetime(2026, 8, 17, 9, 0, 0)))
        self.assertEqual(len(pushed), 1)
        self.assertIn("明天", self.ctx.sent[0][1].__str__())
        # 同年不再预告
        pushed2 = self._run(self.p._check_birthdays(datetime(2026, 8, 17, 12, 0, 0)))
        self.assertEqual(len(pushed2), 0)

    def test_lunar_birthday_year_change(self):
        # 2026 中秋 = 2026-09-25；2027 中秋 = 2027-09-15
        self.set_now(datetime(2026, 9, 25, 9, 0, 0))
        self.p.add_birthday(self.UMO, "小红", 8, 15, True)
        pushed = self._run(self.p._check_birthdays(datetime(2026, 9, 25, 9, 0, 0)))
        self.assertEqual(len(pushed), 1)
        # 次年再次触发（年份去重键是农历年的当年）
        pushed2 = self._run(self.p._check_birthdays(datetime(2027, 9, 15, 9, 0, 0)))
        self.assertEqual(len(pushed2), 1)

    def test_advance_disabled(self):
        self.p.config["birthday_advance_days"] = 0
        self.set_now(datetime(2026, 8, 17, 8, 0, 0))
        self.p.add_birthday(self.UMO, "小明", 8, 18, False)
        pushed = self._run(self.p._check_birthdays(datetime(2026, 8, 17, 9, 0, 0)))
        self.assertEqual(len(pushed), 0)


class TestCountdown(MemoExtraTestBase):
    """倒计时：增删查、每日播报、到期标记"""

    UMO = "default:GroupMessage:123"

    def test_crud(self):
        self.p.add_countdown(self.UMO, "项目上线", __import__("datetime").date(2026, 12, 31))
        items = self.p.list_countdowns(self.UMO)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["target"], "2026-12-31")
        self.assertTrue(self.p.delete_countdown(self.UMO, 1))
        self.assertFalse(self.p.delete_countdown(self.UMO, 1))

    def test_daily_report_before_time(self):
        self.p.add_countdown(self.UMO, "项目上线", __import__("datetime").date(2026, 12, 31))
        # 未到 09:00 不播报
        pushed = self._run(self.p._check_countdowns(datetime(2026, 8, 18, 8, 0, 0)))
        self.assertEqual(len(pushed), 0)

    def test_daily_report_after_time(self):
        self.p.add_countdown(self.UMO, "项目上线", __import__("datetime").date(2026, 12, 31))
        pushed = self._run(self.p._check_countdowns(datetime(2026, 8, 18, 9, 0, 0)))
        self.assertEqual(len(pushed), 1)
        self.assertIn("135", self.ctx.sent[0][1].__str__())
        # 同日只播报一次
        pushed2 = self._run(self.p._check_countdowns(datetime(2026, 8, 18, 20, 0, 0)))
        self.assertEqual(len(pushed2), 0)

    def test_due_marks_done(self):
        self.p.add_countdown(self.UMO, "项目上线", __import__("datetime").date(2026, 8, 18))
        pushed = self._run(self.p._check_countdowns(datetime(2026, 8, 18, 9, 0, 0)))
        self.assertEqual(len(pushed), 1)
        self.assertIn("到期", self.ctx.sent[0][1].__str__())
        item = self.p.list_countdowns(self.UMO)[0]
        self.assertTrue(item["done"])
        # 已完成后不再推送
        pushed2 = self._run(self.p._check_countdowns(datetime(2026, 8, 19, 9, 0, 0)))
        self.assertEqual(len(pushed2), 0)

    def test_bad_config_time(self):
        self.p.config["countdown_push_time"] = "abc"
        self.p.add_countdown(self.UMO, "x", __import__("datetime").date(2026, 12, 31))
        pushed = self._run(self.p._check_countdowns(datetime(2026, 8, 18, 9, 0, 0)))
        self.assertEqual(len(pushed), 1)


class TestHabit(MemoExtraTestBase):
    """习惯打卡：增删、打卡、连续天数、晚间提醒"""

    UMO = "default:GroupMessage:123"

    def test_crud(self):
        self.p.add_habit(self.UMO, "读书")
        self.assertEqual(len(self.p.list_habits(self.UMO)), 1)
        self.assertTrue(self.p.delete_habit(self.UMO, 1))
        self.assertEqual(len(self.p.list_habits(self.UMO)), 0)

    def test_check_streak(self):
        self.set_now(datetime(2026, 8, 17, 10, 0, 0))
        self.p.add_habit(self.UMO, "读书")
        item, first = self.p.check_habit(self.UMO, "读书")
        self.assertTrue(first)
        self.assertEqual(item["streak"], 1)
        # 同日重复打卡不增加
        item2, first2 = self.p.check_habit(self.UMO, "读书")
        self.assertFalse(first2)
        self.assertEqual(item2["streak"], 1)
        # 次日打卡 +1
        self.set_now(datetime(2026, 8, 18, 10, 0, 0))
        item3, first3 = self.p.check_habit(self.UMO, "读书")
        self.assertTrue(first3)
        self.assertEqual(item3["streak"], 2)
        self.assertEqual(item3["best"], 2)
        # 断一天后重新计
        self.set_now(datetime(2026, 8, 20, 10, 0, 0))
        item4, _ = self.p.check_habit(self.UMO, "读书")
        self.assertEqual(item4["streak"], 1)
        self.assertEqual(item4["total"], 3)
        self.assertEqual(item4["best"], 2)

    def test_check_unknown(self):
        self.assertIsNone(self.p.check_habit(self.UMO, "不存在"))

    def test_evening_reminder(self):
        self.set_now(datetime(2026, 8, 18, 10, 0, 0))
        self.p.add_habit(self.UMO, "读书")
        self.p.add_habit(self.UMO, "跑步")
        self.p.check_habit(self.UMO, "读书")
        # 22 点前不提醒
        pushed = self._run(self.p._check_habits(datetime(2026, 8, 18, 21, 0, 0)))
        self.assertEqual(len(pushed), 0)
        # 22 点提醒未打卡的
        pushed2 = self._run(self.p._check_habits(datetime(2026, 8, 18, 22, 0, 0)))
        self.assertEqual(len(pushed2), 1)
        self.assertIn("跑步", self.ctx.sent[0][1].__str__())
        self.assertNotIn("读书", self.ctx.sent[0][1].__str__())
        # 当天只提醒一次
        pushed3 = self._run(self.p._check_habits(datetime(2026, 8, 18, 23, 0, 0)))
        self.assertEqual(len(pushed3), 0)

    def test_reminder_disabled(self):
        self.p.config["habit_remind_enabled"] = False
        self.set_now(datetime(2026, 8, 18, 10, 0, 0))
        self.p.add_habit(self.UMO, "读书")
        pushed = self._run(self.p._check_habits(datetime(2026, 8, 18, 22, 0, 0)))
        self.assertEqual(len(pushed), 0)


class TestLater(MemoExtraTestBase):
    """稍后提醒：时间短语解析与提醒创建"""

    UMO = "default:GroupMessage:123"

    def test_parse_minutes(self):
        with patch("astrbot_plugin_memo.main.datetime") as mdt:
            mdt.now.return_value = datetime(2026, 8, 18, 10, 0, 0)
            mdt.side_effect = lambda *a, **kw: __import__("datetime").datetime(*a, **kw)
            r = MemoPlugin.parse_later("30分钟后")
            self.assertEqual(r, "10:30")
            r2 = MemoPlugin.parse_later("5 分钟")
            self.assertEqual(r2, "10:05")

    def test_parse_hours(self):
        with patch("astrbot_plugin_memo.main.datetime") as mdt:
            mdt.now.return_value = datetime(2026, 8, 18, 10, 0, 0)
            mdt.side_effect = lambda *a, **kw: __import__("datetime").datetime(*a, **kw)
            r = MemoPlugin.parse_later("2小时后")
            self.assertEqual(r, "12:00")

    def test_parse_absolute(self):
        self.assertEqual(MemoPlugin.parse_later("18:30"), "18:30")
        self.assertEqual(MemoPlugin.parse_later("明天 9 点"), "09:00")
        self.assertEqual(MemoPlugin.parse_later("明天 18:30"), "18:30")
        self.assertEqual(MemoPlugin.parse_later("今晚 8 点"), "20:00")
        self.assertEqual(MemoPlugin.parse_later("8点半"), "08:30")
        self.assertEqual(MemoPlugin.parse_later("15点"), "15:00")

    def test_parse_invalid(self):
        self.assertIsNone(MemoPlugin.parse_later("后天"))
        self.assertIsNone(MemoPlugin.parse_later("25点"))
        self.assertIsNone(MemoPlugin.parse_later("60分钟前"))
        self.assertIsNone(MemoPlugin.parse_later(None))

    def test_parse_reminder_cmd(self):
        self.set_now(datetime(2026, 8, 18, 10, 0, 0))
        with patch("astrbot_plugin_memo.main.datetime") as mdt:
            mdt.now.return_value = datetime(2026, 8, 18, 10, 0, 0)
            mdt.side_effect = lambda *a, **kw: __import__("datetime").datetime(*a, **kw)
            r = self.p.parse_later_reminder("30分钟后 取快递")
        self.assertIsNotNone(r)
        hour, minute, content = r
        self.assertEqual((hour, minute), (10, 30))
        self.assertEqual(content, "取快递")
        self.assertIsNone(self.p.parse_later_reminder("取快递"))
        self.assertIsNone(self.p.parse_later_reminder(None))

    def test_add_via_later(self):
        self.set_now(datetime(2026, 8, 18, 10, 0, 0))
        with patch("astrbot_plugin_memo.main.datetime") as mdt:
            mdt.now.return_value = datetime(2026, 8, 18, 10, 0, 0)
            mdt.side_effect = lambda *a, **kw: __import__("datetime").datetime(*a, **kw)
            hour, minute, content = self.p.parse_later_reminder("30分钟后 取快递")
        rem = self.p.add_reminder(self.UMO, "once", hour, minute, None, content)
        self.assertEqual(rem["content"], "取快递")
        # 10:30 到期触发
        self.set_now(datetime(2026, 8, 18, 10, 30, 0))
        pushed = self._run(self.p._check_and_push())
        self.assertEqual(len(pushed), 1)
        self.assertIn("取快递", self.ctx.sent[0][1].__str__())


class TestIntegration(MemoExtraTestBase):
    """后台循环集成：生日 + 倒计时 + 打卡同时存在"""

    UMO = "default:GroupMessage:123"

    def test_check_and_push_all(self):
        now = datetime(2026, 8, 18, 22, 30, 0)
        self.set_now(now)
        # 生日（今天 8/18）
        self.p.add_birthday(self.UMO, "小明", 8, 18, False)
        # 倒计时（未到期）
        self.p.add_countdown(self.UMO, "上线", __import__("datetime").date(2026, 12, 31))
        # 习惯（未打卡）
        self.p.add_habit(self.UMO, "读书")
        pushed = self._run(self.p._check_and_push())
        # 生日 + 倒计时 + 习惯提醒 = 3 条
        self.assertEqual(len(pushed), 3)
        self.assertEqual(len(self.ctx.sent), 3)
        # 再次检查无新增（全部去重）
        pushed2 = self._run(self.p._check_and_push())
        self.assertEqual(len(pushed2), 0)

    def test_empty_no_push(self):
        pushed = self._run(self.p._check_and_push())
        self.assertEqual(len(pushed), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)