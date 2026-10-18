"""料理配方只有一页时的翻页回归：真实资源与内核，内存截图，不连接游戏。

配方少的账号一页就放得下，Swip_Page2 下滑后第一页特征还在原位。修复前 MateProtec
经 [JumpBack] 反复命中 Swip_Page2，任务无限下滑；修复后下滑一次即停用 Swip_Page2。

运行：python -B tools/verify_arbitrage_cooking_single_page.py -v
"""

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image
from maa.controller import CustomController
from maa.define import LoggingLevelEnum
from maa.library import Library
from maa.resource import Resource
from maa.tasker import Tasker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

# 在进程内直接调用真实内核；业务类稍后向 Resource 注册，不启动 AgentServer。
Library.version()
from maa.agent.agent_server import AgentServer

with patch.object(AgentServer, "custom_action", return_value=lambda cls: cls), \
     patch.object(AgentServer, "custom_recognition", return_value=lambda cls: cls), \
     patch.object(AgentServer, "_set_api_properties"), \
     patch.object(AgentServer, "context_sink", return_value=lambda cls: cls):
    from action.pipeline_manager import PatchPipeline


PROTECTION = "Arbitrage_Cooking_MateProtec"
SWIPE = "Arbitrage_Cooking_Swip_Page2"
STUCK = "Arbitrage_Cooking_Swip_Page2_Stuck"
PAGE_ONE_ICON = ROOT / "assets/resource/base/image/Shop/RecipeList/料理_炸三文鱼便当.png"
ICON_AT = (599, 212)  # 实机日志里第一页特征的识别框左上角
MAX_SWIPES = 3


def page_one_screen():
    screen = np.zeros((720, 1280, 3), dtype=np.uint8)
    icon = np.asarray(Image.open(PAGE_ONE_ICON).convert("RGB"))[:, :, ::-1]
    x, y = ICON_AT
    screen[y:y + icon.shape[0], x:x + icon.shape[1]] = icon
    return screen


class RecipeListController(CustomController):
    """scrolls=False：列表滑不动，画面一直是第一页；True：下滑一次后第一页离开画面。"""

    def __init__(self, scrolls):
        super().__init__()
        self.scrolls = scrolls
        self.swipes = 0
        self.stop = None
        self.page_one = page_one_screen()

    def connect(self): return True
    def request_uuid(self): return "arbitrage-cooking-single-page-offline"
    def get_features(self): return 0
    def start_app(self, *args): return False
    def stop_app(self, *args): return False
    def click(self, *args): return False
    def touch_down(self, *args): return False
    def touch_move(self, *args): return False
    def touch_up(self, *args): return False
    def click_key(self, *args): return False
    def input_text(self, *args): return False
    def key_down(self, *args): return False
    def key_up(self, *args): return False

    def screencap(self):
        if self.scrolls and self.swipes:
            return np.zeros_like(self.page_one)
        return self.page_one

    def swipe(self, *args):
        self.swipes += 1
        # 修复前会一直下滑；超过上限就停掉任务，交给断言报出次数
        if self.swipes > MAX_SWIPES and self.stop:
            self.stop()
        return True


class CookingSinglePageTests(unittest.TestCase):
    def test_single_page_list_swipes_once_then_stops(self):
        controller, names = self.run_protection(scrolls=False)
        self.assertEqual(controller.swipes, 1)
        self.assertEqual(names.count(SWIPE), 1)
        self.assertIn(STUCK, names)

    def test_two_page_list_does_not_disable_swipe(self):
        controller, names = self.run_protection(scrolls=True)
        self.assertEqual(controller.swipes, 1)
        self.assertEqual(names.count(SWIPE), 1)
        self.assertNotIn(STUCK, names)

    def run_protection(self, scrolls):
        # MaaFramework keeps its log handle open until process exit on Windows.
        with tempfile.TemporaryDirectory(prefix="mfabd2-cooking-page-", ignore_cleanup_errors=True) as directory:
            Tasker.set_log_dir(directory)
            Tasker.set_stdout_level(LoggingLevelEnum.Off)
            Tasker.set_save_draw(False)
            Tasker.set_save_on_error(False)
            resource = Resource()
            controller = RecipeListController(scrolls)
            tasker = Tasker()
            try:
                self.assertTrue(resource.post_bundle(ROOT / "assets/resource/base").wait().succeeded)
                self.assertTrue(resource.override_pipeline({PROTECTION: {"timeout": 300, "rate_limit": 0}}))
                self.assertTrue(resource.register_custom_action("PatchPipeline", PatchPipeline()))
                self.assertTrue(controller.post_connection().wait().succeeded)
                self.assertTrue(tasker.bind(resource, controller))
                controller.stop = tasker.post_stop
                detail = tasker.post_task(PROTECTION).wait().get()
                return controller, [node.name for node in detail.nodes]
            finally:
                tasker.post_stop().wait()
                del tasker
                del controller
                resource.clear()
                Tasker.set_log_dir("")


if __name__ == "__main__":
    unittest.main()
