"""跑商寻路按钮死循环回归：真实资源与内核，内存截图，不连接游戏。

修复前 Arbitrage_NviGuide_Butt 的 next 含自身且无界：游戏不响应点击时寻路列表打不开，
按钮一直认得到，任务永不结束（2026-10-19 12:07–12:10 实测连点 172 次，重启游戏后恢复）。
修复后补点 10 次（Butt_Retry 的 max_hit），仍打不开就走 Butt_GiveUp → Global_Null_Panic 重启游戏。

夹具取自 2026-10-19 实机截图，只保留寻路按钮与列表区域，其余填平色：
- nvi_list_closed.png：卡住时的画面，列表关着；
- nvi_list_open.png：重启前手动点开的列表，含「商店」。列表打开后按钮模板认不到。
源码仓库没有 OCR 模型，认「商店」的用例在模型缺失时跳过。

运行：python -B tools/verify_arbitrage_nvi_guide_loop.py -v
"""

from pathlib import Path
import json
import tempfile
import threading
import unittest

import numpy as np
from PIL import Image
from maa.controller import CustomController
from maa.define import LoggingLevelEnum
from maa.resource import Resource
from maa.tasker import Tasker

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "assets/resource/base"
PC = ROOT / "assets/resource/pc"
FIXTURES = Path(__file__).resolve().parent / "fixtures/arbitrage_nvi_guide_loop"

BUTT = "Arbitrage_NviGuide_Butt"
RETRY = "Arbitrage_NviGuide_Butt_Retry"
GIVE_UP = "Arbitrage_NviGuide_Butt_GiveUp"
MERCHANT = "Arbitrage_NviGuide_Merchant_Ocr"
PANIC = "Global_Null_Panic"
CLICKS_LIMIT = 11  # 1 次 + Butt_Retry max_hit 10 次补点
BUTT_ROI = [208, 72, 138, 43]
HAS_OCR = (BASE / "model/ocr/rec.onnx").exists()
WATCHDOG_S = 60


def load(name):
    return np.ascontiguousarray(np.asarray(Image.open(FIXTURES / name).convert("RGB"))[:, :, ::-1])


class NviGuideController(CustomController):
    """寻路按钮点满 opens_after 次后列表打开；None 表示游戏不响应点击，列表永远关着。"""

    def __init__(self, opens_after):
        super().__init__()
        self.closed = load("nvi_list_closed.png")
        self.open = load("nvi_list_open.png")
        self.opens_after = opens_after
        self.clicks = []

    def connect(self): return True
    def request_uuid(self): return "arbitrage-nvi-guide-offline"
    def get_features(self): return 0
    def start_app(self, *args): return True
    def stop_app(self, *args): return True
    def swipe(self, *args): return True
    def touch_down(self, *args): return True
    def touch_move(self, *args): return True
    def touch_up(self, *args): return True
    def click_key(self, *args): return True
    def input_text(self, *args): return True
    def key_down(self, *args): return True
    def key_up(self, *args): return True

    def click(self, x, y):
        self.clicks.append((x, y))
        return True

    def butt_clicks(self):
        x, y, w, h = BUTT_ROI
        return sum(x <= cx < x + w and y <= cy < y + h for cx, cy in self.clicks)

    def screencap(self):
        opened = self.opens_after is not None and self.butt_clicks() >= self.opens_after
        return self.open if opened else self.closed


class NviGuideLoopTests(unittest.TestCase):
    def test_unresponsive_game_gives_up_and_panics(self):
        controller, names, timed_out = self.run_butt(NviGuideController(None))
        self.assertFalse(timed_out)
        self.assertEqual(controller.butt_clicks(), CLICKS_LIMIT)
        self.assertEqual(names[-2:], [GIVE_UP, PANIC])

    @unittest.skipUnless(HAS_OCR, "源码仓库没有 OCR 模型")
    def test_list_opens_first_click(self):
        controller, names, timed_out = self.run_butt(NviGuideController(1))
        self.assertFalse(timed_out)
        self.assertEqual(controller.butt_clicks(), 1)
        self.assertEqual(names[-1], MERCHANT)
        self.assertNotIn(RETRY, names)

    @unittest.skipUnless(HAS_OCR, "源码仓库没有 OCR 模型")
    def test_dropped_clicks_are_retried(self):
        controller, names, timed_out = self.run_butt(NviGuideController(3))
        self.assertFalse(timed_out)
        self.assertEqual(controller.butt_clicks(), 3)
        self.assertEqual(names[-1], MERCHANT)
        self.assertEqual(names.count(RETRY), 2)

    def test_open_list_hides_button(self):
        # 列表打开后 Retry/GiveUp 都认不到，补点不会把已打开的列表点关
        controller, names, timed_out = self.run_butt(NviGuideController(1), entry_only=True)
        self.assertFalse(timed_out)
        self.assertNotIn(BUTT, names)

    def test_pipeline_shape(self):
        base = json.loads((BASE / "pipeline/Arbitrage.json").read_text(encoding="utf-8"))
        pc = json.loads((PC / "pipeline/Arbitrage.json").read_text(encoding="utf-8"))
        self.assertNotIn(BUTT, base[BUTT]["next"])
        self.assertEqual(base[RETRY]["next"], [BUTT])
        self.assertEqual(base[RETRY]["max_hit"], CLICKS_LIMIT - 1)
        self.assertEqual(base[GIVE_UP]["next"], [PANIC])
        # PC 包只覆盖 roi/模板，next 继承 base
        self.assertNotIn("next", pc.get(BUTT, {}))

    def run_butt(self, controller, entry_only=False):
        fixture = {
            BUTT: {"pre_delay": 0, "rate_limit": 0},
            MERCHANT: {"action": "DoNothing", "next": []},
            PANIC: {"action": "DoNothing", "next": []},
        }
        if entry_only:
            # 先点开列表，再从 Butt 进入：认不到按钮就以超时结束
            controller.clicks.append((BUTT_ROI[0] + 1, BUTT_ROI[1] + 1))
            fixture[BUTT].update({"timeout": 0})
        # MaaFramework keeps its log handle open until process exit on Windows.
        with tempfile.TemporaryDirectory(prefix="mfabd2-arbnav-", ignore_cleanup_errors=True) as directory:
            Tasker.set_log_dir(directory)
            Tasker.set_stdout_level(LoggingLevelEnum.Off)
            Tasker.set_save_draw(False)
            Tasker.set_save_on_error(False)
            resource = Resource()
            tasker = Tasker()
            # 修复前按钮会被一直点，看门狗停掉后交给断言报错
            fired = threading.Event()
            watchdog = threading.Timer(WATCHDOG_S, lambda: (fired.set(), tasker.post_stop()))
            try:
                self.assertTrue(resource.post_bundle(BASE).wait().succeeded)
                self.assertTrue(resource.override_pipeline(fixture))
                self.assertTrue(controller.post_connection().wait().succeeded)
                self.assertTrue(tasker.bind(resource, controller))
                watchdog.start()
                detail = tasker.post_task(BUTT).wait().get()
                names = [node.name for node in detail.nodes if node] if detail else []
                return controller, names, fired.is_set()
            finally:
                watchdog.cancel()
                tasker.post_stop().wait()
                del tasker
                resource.clear()
                Tasker.set_log_dir("")


if __name__ == "__main__":
    unittest.main()
