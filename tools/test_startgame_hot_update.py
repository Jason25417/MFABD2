"""Static transition regression: python -m unittest discover -s tools -p test_startgame_hot_update.py"""
import json
from pathlib import Path
import re
import unittest


class HotUpdateTransitions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[1] / 'assets/resource/base/pipeline/StartGame.json'
        cls.nodes = json.loads(path.read_text(encoding='utf-8'))

    def test_running_app_can_resume_hot_update(self):
        following = self.nodes['StartGame_Check_App_Alive']['next']
        for node in ('StartGame_Update_Data', 'StartGame_Update_Data_DownLoading', 'StartGame_Loading'):
            self.assertLess(following.index(node), following.index('Global_Null'))

    def test_launch_entries_can_resume_hot_update(self):
        # 共享 base 启动入口需要接回更新流程；NT PC 的独立入口另行检查。
        for entry in ('StartGame_RunApp', 'StartGame_RunApp_Shell'):
            following = self.nodes[entry]['next']
            for node in ('StartGame_Update_Data', 'StartGame_Update_Data_DownLoading', 'StartGame_Loading'):
                self.assertLess(following.index(node), following.index('StartGame_Logo'), entry)

    def test_nt_pc_window_entry_can_resume_hot_update(self):
        # NT PC 使用独立的窗口检查入口，不经过 base 的 RunApp 分支。
        path = Path(__file__).resolve().parents[1] / 'assets/resource/pc/pipeline/StartGame.json'
        nodes = json.loads(path.read_text(encoding='utf-8'))
        self.assertIn('StartGame_Check_Win32_SetSize', nodes['StartGame_Start']['next'])
        following = nodes['StartGame_Check_Win32_SetSize']['next']
        for node in ('StartGame_Update_Data', 'StartGame_Update_Data_DownLoading', 'StartGame_Loading'):
            self.assertIn(node, following)
            self.assertLess(following.index(node), following.index('StartGame_Logo'))

    def test_download_can_finish_at_loading_screen(self):
        for node in ('StartGame_Update_Data_Click', 'StartGame_Update_Data_DownLoading'):
            self.assertIn('StartGame_Loading', self.nodes[node]['next'])
            self.assertIn('StartGame_TouchStart', self.nodes[node]['next'])

    def test_download_timeout_does_not_request_apk_update(self):
        self.assertEqual(['StartGame_LoadingPage'], self.nodes['StartGame_Update_Data_DownLoading']['on_error'])
        self.assertEqual(['StartGame_UpdateCloseGame'], self.nodes['StartGame_Update_PGM_Click']['next'])

    def test_existing_download_confirmation_is_unchanged(self):
        self.assertEqual(['将下载游戏'], self.nodes['StartGame_Update_Data']['expected'])
        self.assertEqual(['下载'], self.nodes['StartGame_Update_Data_Click']['expected'])
        self.assertEqual('Click', self.nodes['StartGame_Update_Data_Click']['action'])

    def test_pvp_interrupt_ignores_download_dialog(self):
        # PVP_PVPMes 挂在 Global_ToSandBox 的 JumpBack 里，下载框背景的整行文字不能触发它
        path = Path(__file__).resolve().parents[1] / 'assets/resource/base/pipeline/PVP.json'
        expected = json.loads(path.read_text(encoding='utf-8'))['PVP_PVPMes']['expected']
        matches = lambda text: any(re.search(pattern, text) for pattern in expected)
        self.assertFalse(matches('正在确认下载容量100%'))
        self.assertTrue(matches('确认'))

    def test_nt_pc_pvp_interrupt_ignores_download_dialog(self):
        path = Path(__file__).resolve().parents[1] / 'assets/resource/pc/pipeline/PVP.json'
        expected = json.loads(path.read_text(encoding='utf-8'))['PVP_PVPMes']['expected']
        matches = lambda text: any(re.search(pattern, text) for pattern in expected)
        for text in ('正在确认下载容量100%', '正在確認下載容量100%'):
            self.assertFalse(matches(text), text)
        for text in ('确认', '確認'):
            self.assertTrue(matches(text), text)


if __name__ == '__main__':
    unittest.main()
