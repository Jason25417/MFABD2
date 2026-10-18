"""商店退不出去时的死循环回归：真实资源与内核，内存截图，不连接游戏。

游戏卡在转圈、返回按钮次数用尽后，Arbitrage_ShopOut 的 next 全部识别不到。修复前 on_error
走默认 Global_Null，调用方（如 Cooking_MenuPatch）经 [JumpBack] 立刻再进 ShopOut，任务永不结束；
修复后超时次数超过 Global_Null_Exception 的上限，由 Global_Null_Panic 重启游戏。

运行：python -B tools/verify_arbitrage_shopout_stuck.py -v
"""

from pathlib import Path
import tempfile
import threading
import unittest

import numpy as np
from maa.controller import CustomController
from maa.define import LoggingLevelEnum
from maa.library import Library
from maa.resource import Resource
from maa.tasker import Tasker

ROOT = Path(__file__).resolve().parents[1]
Library.version()

SHOP_OUT = "Arbitrage_ShopOut"
EXCEPTION = "Global_Null_Exception"
PANIC = "Global_Null_Panic"
CALLER = "test_shopout_caller"
EXCEPTION_LIMIT = 35  # Global_Null_Exception.max_hit
PANIC_LIMIT = 3       # Global_Null_Panic.max_hit
SHOP_MARK = [0, 0, 40, 40]
WATCHDOG_S = 60


class FrozenController(CustomController):
    """游戏无响应：画面不变，商店关闭、返回、转场、箱庭等出口都认不到。

    底图用固定种子的中灰噪声，不用纯黑：纯黑会被 Global_WaitingForLoading 当成转场黑屏。
    左上角白块代表「还在商店里」；restart_leaves_shop 为 True 时，重启游戏后白块消失。
    """

    def __init__(self, restart_leaves_shop):
        super().__init__()
        self.restart_leaves_shop = restart_leaves_shop
        self.stopped = []
        self.in_shop = np.random.default_rng(0).integers(40, 200, (720, 1280, 3), dtype=np.uint8)
        self.left_shop = self.in_shop.copy()
        x, y, w, h = SHOP_MARK
        self.in_shop[y:y + h, x:x + w] = 255

    def connect(self): return True
    def request_uuid(self): return "arbitrage-shopout-stuck-offline"
    def get_features(self): return 0
    def start_app(self, *args): return False
    def click(self, *args): return True
    def swipe(self, *args): return True
    def touch_down(self, *args): return True
    def touch_move(self, *args): return True
    def touch_up(self, *args): return True
    def click_key(self, *args): return True
    def input_text(self, *args): return True
    def key_down(self, *args): return True
    def key_up(self, *args): return True

    def screencap(self):
        if self.stopped and self.restart_leaves_shop:
            return self.left_shop
        return self.in_shop

    def stop_app(self, intent):
        self.stopped.append(intent)
        return True


class ShopOutStuckTests(unittest.TestCase):
    def test_frozen_shop_restarts_game_once_and_ends(self):
        controller, names, timed_out = self.run_caller(restart_leaves_shop=True)
        self.assertFalse(timed_out)
        self.assertEqual(controller.stopped, ["com.neowizgames.game.browndust2"])
        self.assertEqual(names.count(EXCEPTION), EXCEPTION_LIMIT)
        self.assertEqual(names.count(PANIC), 1)
        self.assertEqual(names.count(SHOP_OUT), EXCEPTION_LIMIT + 1)

    def test_still_frozen_after_restart_ends_after_panic_limit(self):
        controller, names, timed_out = self.run_caller(restart_leaves_shop=False)
        self.assertFalse(timed_out)
        self.assertEqual(len(controller.stopped), PANIC_LIMIT)
        self.assertEqual(names.count(PANIC), PANIC_LIMIT)

    def test_normal_exit_does_not_touch_exception(self):
        # 能回箱庭时 ShopOut 正常收尾；回箱庭后画面已不是商店，这里用 max_hit 让调用方只进一次
        controller, names, timed_out = self.run_caller(restart_leaves_shop=True, extra={
            SHOP_OUT: {"max_hit": 1},
            "Global_ToSandBox_Enter": {"recognition": "DirectHit"},
        })
        self.assertFalse(timed_out)
        self.assertIn("Global_ToSandBox_Enter", names)
        self.assertNotIn(EXCEPTION, names)
        self.assertNotIn(PANIC, names)
        self.assertEqual(controller.stopped, [])

    def run_caller(self, restart_leaves_shop, extra=None):
        fixture = {
            # 调用方与 Cooking_MenuPatch 同形：[JumpBack] 进 ShopOut，回来后再识别
            CALLER: {"next": [f"[JumpBack]{SHOP_OUT}"], "timeout": 1, "rate_limit": 0, "on_error": []},
            # 内存截图跑不了 OCR，用左上角白块代表「画面仍是商店」
            SHOP_OUT: {"recognition": "ColorMatch", "roi": SHOP_MARK, "lower": [250, 250, 250],
                       "upper": [255, 255, 255], "count": SHOP_MARK[2] * SHOP_MARK[3],
                       "timeout": 1, "rate_limit": 0},
            EXCEPTION: {"rate_limit": 0},
            PANIC: {"post_delay": 0, "rate_limit": 0},
            "StartGame_Check_App_Alive": {"action": "DoNothing", "next": [], "on_error": []},
        }
        for name, changes in (extra or {}).items():
            fixture.setdefault(name, {}).update(changes)
        # MaaFramework keeps its log handle open until process exit on Windows.
        with tempfile.TemporaryDirectory(prefix="mfabd2-shopout-", ignore_cleanup_errors=True) as directory:
            Tasker.set_log_dir(directory)
            Tasker.set_stdout_level(LoggingLevelEnum.Off)
            Tasker.set_save_draw(False)
            Tasker.set_save_on_error(False)
            resource = Resource()
            controller = FrozenController(restart_leaves_shop)
            tasker = Tasker()
            # 修复前任务永不结束，看门狗停掉后交给断言报错
            fired = threading.Event()
            watchdog = threading.Timer(WATCHDOG_S, lambda: (fired.set(), tasker.post_stop()))
            try:
                self.assertTrue(resource.post_bundle(ROOT / "assets/resource/base").wait().succeeded)
                self.assertTrue(resource.override_pipeline(fixture))
                self.assertTrue(controller.post_connection().wait().succeeded)
                self.assertTrue(tasker.bind(resource, controller))
                watchdog.start()
                detail = tasker.post_task(CALLER).wait().get()
                return controller, [node.name for node in detail.nodes], fired.is_set()
            finally:
                watchdog.cancel()
                tasker.post_stop().wait()
                del tasker
                resource.clear()
                Tasker.set_log_dir("")


if __name__ == "__main__":
    unittest.main()
