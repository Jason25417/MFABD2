"""吸取技能「周围没有可以吸收的拾取物」死循环回归：真实资源与内核，内存截图，不连接游戏。

修复前 Collect_Skill3_2 的 next 含自身且无界：同一卡带当周中断后重跑，已吸过的点没有拾取物，
点吸取只弹提示、计数不涨，但吸取图标的可用色核仍亮，任务永不结束（2026-10-19 沙漠之花 tp1
实测 156 连点，计数一直 8/10）。修复后认到提示就跳过吸取；没认到提示时每次调用技能组最多补点 5 次。

夹具 skill_bar_no_target_toast.png 取自 2026-10-19 实机截图，只保留吸取图标、第 3 页图钉与提示框
三块，其余填平色。源码仓库没有 OCR 模型，认提示的用例在模型缺失时跳过。

运行：python -B tools/verify_collect_absorb_no_target.py -v
"""

from pathlib import Path
import importlib.util
import json
import sys
import tempfile
import threading
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
BASE = ROOT / "assets/resource/base"
FIXTURE = Path(__file__).resolve().parent / "fixtures/collect_absorb_no_target/skill_bar_no_target_toast.png"
sys.path.insert(0, str(ROOT / "agent"))
Library.version()
from maa.agent.agent_server import AgentServer
# Native tests register callbacks on Resource, not on an AgentServer process.
# 只加载 counter.py，不经 recognition/__init__ 拉起整个识别包
with patch.object(AgentServer, "custom_recognition", return_value=lambda cls: cls), \
     patch.object(AgentServer, "custom_action", return_value=lambda cls: cls):
    _spec = importlib.util.spec_from_file_location("counter", ROOT / "agent/recognition/counter.py")
    counter = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(counter)

ENTRY = "Collect_Skill_Tap3_Enable"
ABSORB = "Collect_Skill3_2"
USED = "Collect_Skill3_2_Used"
NO_TARGET = "Collect_Skill3_2_NoTarget"
RETRY = "Collect_Skill3_2_Retry"
RETRY_COUNT = "Collect_Skill3_2_Retry_Count"
GIVE_UP = "Collect_Skill3_2_GiveUp"
CLICKS_LIMIT = 6  # 1 次 + CheckTag max 5 次补点
ABSORB_ICON = [933, 526, 75, 73]
ABSORB_DOT = [954, 555, 8, 13]  # 可用色核；吸完后变灰
TOAST = [440, 140, 400, 55]
HAS_OCR = (BASE / "model/ocr/rec.onnx").exists()
WATCHDOG_S = 60


def load_frames():
    with_toast = np.ascontiguousarray(np.asarray(Image.open(FIXTURE).convert("RGB"))[:, :, ::-1])
    without_toast = with_toast.copy()
    x, y, w, h = TOAST
    without_toast[y:y + h, x:x + w] = (64, 60, 60)
    used = without_toast.copy()
    x, y, w, h = ABSORB_DOT
    used[y:y + h, x:x + w] = 148  # Collect_Skill3_2_Used 的灰色色核
    return with_toast, without_toast, used


class SkillBarController(CustomController):
    """技能栏画面：点吸取之前一律是 before，点过之后换成 after。"""

    def __init__(self, before, after):
        super().__init__()
        self.before = before
        self.after = after
        self.clicks = []

    def connect(self): return True
    def request_uuid(self): return "collect-absorb-no-target-offline"
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

    def absorb_clicks(self):
        x, y, w, h = ABSORB_ICON
        return sum(x <= cx < x + w and y <= cy < y + h for cx, cy in self.clicks)

    def screencap(self):
        return self.after if self.absorb_clicks() else self.before


class AbsorbNoTargetTests(unittest.TestCase):
    @unittest.skipUnless(HAS_OCR, "源码仓库没有 OCR 模型")
    def test_no_target_toast_skips_absorb(self):
        with_toast, without_toast, _ = load_frames()
        (controller, names, timed_out), = self.run_groups(SkillBarController(without_toast, with_toast))
        self.assertFalse(timed_out)
        self.assertEqual(controller.absorb_clicks(), 1)
        self.assertEqual(names[-1], NO_TARGET)

    def test_unresponsive_absorb_gives_up(self):
        _, without_toast, _ = load_frames()
        (controller, names, timed_out), = self.run_groups(SkillBarController(without_toast, without_toast))
        self.assertFalse(timed_out)
        self.assertEqual(controller.absorb_clicks(), CLICKS_LIMIT)
        self.assertEqual(names[-1], GIVE_UP)

    def test_retry_budget_resets_each_skill_group(self):
        _, without_toast, _ = load_frames()
        runs = self.run_groups(*(SkillBarController(without_toast, without_toast) for _ in range(2)))
        for controller, names, timed_out in runs:
            self.assertFalse(timed_out)
            self.assertEqual(controller.absorb_clicks(), CLICKS_LIMIT)

    def test_working_absorb_clicks_once(self):
        _, without_toast, used = load_frames()
        (controller, names, timed_out), = self.run_groups(SkillBarController(without_toast, used))
        self.assertFalse(timed_out)
        self.assertEqual(controller.absorb_clicks(), 1)
        self.assertEqual(names[-1], USED)
        self.assertNotIn(RETRY_COUNT, names)

    def test_new_exits_reuse_used_exit_and_onlyonce_patch_is_in_sync(self):
        skills = json.loads((BASE / "pipeline/Collect_skills.json").read_text(encoding="utf-8"))
        launcher = json.loads((BASE / "pipeline/Collect_Launcher.json").read_text(encoding="utf-8"))
        used_next = skills[USED]["next"]
        self.assertEqual(skills[NO_TARGET]["next"], used_next)
        self.assertEqual(skills[GIVE_UP]["next"], used_next)
        self.assertEqual(skills[ABSORB]["next"][:2], [NO_TARGET, RETRY])
        patches = launcher["Collect_OnlyOnce_Switch"]["custom_action_param"]["patches"]
        # 快照整份替换 next：两个新分支必须排在最前，否则单章模式下认不到提示、也不补点
        self.assertEqual(patches[ABSORB]["next"][:2], [NO_TARGET, RETRY])
        self.assertNotIn(ABSORB, patches[ABSORB]["next"])
        for name in (NO_TARGET, GIVE_UP):
            self.assertEqual(patches[name]["next"], ["[JumpBack]Collect_OnlyOnce_SkillUsed", *used_next])

    def run_groups(self, *controllers):
        """同一 Resource、同一计数器上依次跑若干次技能组（每次都从 Tap3_Enable 进入）。"""
        counter.TAG_STORE.clear()
        fixture = {
            ENTRY: {"next": [ABSORB]},  # 跳过探查，直接到吸取
            ABSORB: {"post_delay": 0, "rate_limit": 0},
            NO_TARGET: {"next": []},
            GIVE_UP: {"next": []},
            USED: {"next": []},
        }
        results = []
        # MaaFramework keeps its log handle open until process exit on Windows.
        with tempfile.TemporaryDirectory(prefix="mfabd2-absorb-", ignore_cleanup_errors=True) as directory:
            Tasker.set_log_dir(directory)
            Tasker.set_stdout_level(LoggingLevelEnum.Off)
            Tasker.set_save_draw(False)
            Tasker.set_save_on_error(False)
            resource = Resource()
            try:
                self.assertTrue(resource.post_bundle(BASE).wait().succeeded)
                self.assertTrue(resource.override_pipeline(fixture))
                self.assertTrue(resource.register_custom_recognition("CheckTag", counter.CheckTag()))
                self.assertTrue(resource.register_custom_action("UpdateTag", counter.UpdateTag()))
                self.assertTrue(resource.register_custom_action("ResetTag", counter.ResetTag()))
                for controller in controllers:
                    results.append(self.run_once(resource, controller))
                return results
            finally:
                resource.clear()
                Tasker.set_log_dir("")

    def run_once(self, resource, controller):
        tasker = Tasker()
        # 修复前吸取会被一直点，看门狗停掉后交给断言报错
        fired = threading.Event()
        watchdog = threading.Timer(WATCHDOG_S, lambda: (fired.set(), tasker.post_stop()))
        try:
            self.assertTrue(controller.post_connection().wait().succeeded)
            self.assertTrue(tasker.bind(resource, controller))
            watchdog.start()
            detail = tasker.post_task(ENTRY).wait().get()
            return controller, [node.name for node in detail.nodes if node], fired.is_set()
        finally:
            watchdog.cancel()
            tasker.post_stop().wait()
            del tasker


if __name__ == "__main__":
    unittest.main()
