"""Verify price-list custom callbacks through real MaaFw, without a game."""

import json
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from maa.controller import CustomController
from maa.custom_action import CustomAction
from maa.library import Library
from maa.resource import Resource
from maa.tasker import Tasker
from maa.toolkit import Toolkit

Library.version()  # Select local framework mode before importing AgentServer.
from maa.agent.agent_server import AgentServer

# Embedded Python may omit the script directory from sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent))

# These tests register callbacks on a local Resource, not an AgentServer process.
with patch.object(AgentServer, "_set_api_properties"), \
     patch.object(AgentServer, "register_custom_action", return_value=True), \
     patch.object(AgentServer, "register_custom_recognition", return_value=True), \
     patch.object(AgentServer, "context_sink", return_value=lambda cls: cls):
    from verify_arbitrage_preview import ROOT, item
    from action import arbitrage_result as ar
    from action.smart_action import SmartAction


class Shop(CustomController):
    def connect(self): return True
    def request_uuid(self): return "pricelist-routing-offline"
    def screencap(self): return np.zeros((720, 1280, 3), dtype=np.uint8)
    def swipe(self, x1, y1, x2, y2, duration): return True
    def click(self, x, y): return False
    def start_app(self, intent): return False
    def stop_app(self, intent): return False
    def touch_down(self, contact, x, y, pressure): return False
    def touch_move(self, contact, x, y, pressure): return False
    def touch_up(self, contact): return False
    def click_key(self, keycode): return False
    def input_text(self, text): return False
    def key_down(self, keycode): return False
    def key_up(self, keycode): return False


class PriceListRoutingTests(unittest.TestCase):
    def run_scan(self, pc=False, missed=0, capture_failure=False):
        if pc and not (ROOT / "assets/resource/pc/pipeline/Arbitrage.json").is_file():
            self.skipTest("PC arbitrage overlay is not present on this branch")
        with tempfile.TemporaryDirectory(prefix="mfabd2-pricelist-routing-") as temp:
            self.assertTrue(Toolkit.init_option(temp, {"logging": False}))
            self.assertTrue(Tasker.set_save_on_error(False))
            resource = Resource()
            self.assertTrue(resource.post_bundle(ROOT / "assets/resource/base").wait().succeeded)
            if pc:
                self.assertTrue(resource.post_bundle(ROOT / "assets/resource/pc").wait().succeeded)
            shop = Shop()
            state = SimpleNamespace(page=0, swipes=0, calibrations=0, recos=[], scan=None, events=[])
            shop.screencap = lambda: np.full(
                (720, 1280, 3), state.page * 100 + state.calibrations * 10, dtype=np.uint8
            )

            def swipe(*args):
                state.swipes += 1
                state.events.append("swipe")
                if state.swipes > missed:
                    state.page = 1
                return True

            shop.swipe = swipe
            shop.touch_down = lambda *args: True
            shop.touch_move = lambda *args: True
            shop.touch_up = lambda contact: swipe()
            self.assertTrue(shop.post_connection().wait().succeeded)
            tasker = Tasker()
            self.assertTrue(tasker.bind(resource, shop))

            class ObservedSmartAction(SmartAction):
                def run(self, context, argv):
                    state.recos.append(argv.reco_detail.reco_id)
                    return super().run(context, argv)

                def _diff(self, before, after, tag):
                    state.events.append("compare")
                    return super()._diff(before, after, tag)

            class Probe(CustomAction):
                def run(self, context, argv):
                    if argv.node_name == "Arbitrage_Swip_Calibration_Hub":
                        state.calibrations += 1
                        state.events.append("calibrate")
                        return True
                    controller = ar.ArbitrageSellController()

                    def read_page(ctx):
                        state.events.append("read")
                        return [item("炒蘑菇" if state.page == 0 else "咖啡豆")]

                    controller._parse_current_page = read_page
                    state.scan = controller._scan_price_list(
                        context, max_scan_pages=8, stop_at_non_max=False
                    )
                    return True

            smart = ObservedSmartAction()
            self.assertTrue(resource.register_custom_action("SmartAction", smart))
            self.assertTrue(resource.register_custom_action("test_pricelist_probe", Probe()))
            fast = {"pre_delay": 0, "post_delay": 0, "rate_limit": 0}
            params = deepcopy(resource.get_node_object("Agt_PriceList_Swip").action.param.custom_action_param)
            if not isinstance(params, dict):
                params = json.loads(params)
            params["proxy_override"].update(fast, duration=1, end_hold=0)
            override = {
                "test_pricelist_scan": {**fast, "action": "Custom", "custom_action": "test_pricelist_probe"},
                "Agt_PriceList_Swip": {**fast, "custom_action_param": params},
                "Arbitrage_Swip_Calibration_Hub": {
                    **fast, "action": "Custom", "custom_action": "test_pricelist_probe", "next": []
                },
            }
            with patch("action.smart_action.time.sleep") as settle:
                if capture_failure:
                    with patch.object(smart, "_grab", return_value=None):
                        result = tasker.post_task("test_pricelist_scan", override).wait()
                else:
                    result = tasker.post_task("test_pricelist_scan", override).wait()
                self.assertTrue(result.succeeded)
                self.assertTrue(state.recos, "Native callback must enter SmartAction.run")
                self.assertTrue(all(reco > 0 for reco in state.recos))
                self.assertTrue(all(call.args == (.3,) for call in settle.call_args_list))
                self.assertEqual(settle.call_count, state.swipes)
            tasker.post_stop().wait()
            return state

    def test_callback_retries_and_returns_to_page_parser(self):
        for pc in (False, True):
            for missed in (0, 1, 2):
                with self.subTest(pc=pc, missed=missed):
                    state = self.run_scan(pc=pc, missed=missed)
                    self.assertEqual(state.swipes, missed + 1 + 3)
                    self.assertEqual(state.calibrations, 2)
                    self.assertEqual(state.events, ["read"] + ["swipe", "compare"] * (missed + 1)
                                     + ["calibrate", "read"] + ["swipe", "compare"] * 3
                                     + ["calibrate", "read"])
                    self.assertEqual([row["name"] for row in state.scan["items"]], ["炒蘑菇", "咖啡豆"])
                    self.assertTrue(state.scan["full_list_complete"])

    def test_custom_failure_is_not_swallowed_by_default_on_error(self):
        for pc in (False, True):
            with self.subTest(pc=pc):
                state = self.run_scan(pc=pc, capture_failure=True)
                self.assertEqual(state.swipes, 0)
                self.assertEqual(state.calibrations, 0)
                self.assertEqual(state.events, ["read"])
                self.assertEqual(state.scan["termination_reason"], "swipe_failed")
                self.assertFalse(state.scan["full_list_complete"])


if __name__ == "__main__":
    unittest.main()
