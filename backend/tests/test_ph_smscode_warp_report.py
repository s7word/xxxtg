"""runner 出口统计：槽位绑定日志也要计入 unique_ips，IP=- 不当真地址。"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.chdir(REPO_ROOT)

from backend.scripts.run_ph_smscode_warp import enrich  # noqa: E402


class TestPhWarpEgressReport(unittest.TestCase):
    def test_slot_bind_alignment_line_counts_as_ip(self):
        row = enrich(
            {},
            {
                "logs": [
                    "[代理槽位] 1:1 绑定预分配出口 res.proxy-seller.com:10001",
                    "[多径中继网关] 出口拓扑对齐: IP=185.246.1.10 国家=PT "
                    "(手机号区域/语言/时区将按 PT 对齐)",
                ]
            },
        )
        self.assertEqual(row["egress_ip"], "185.246.1.10")
        self.assertEqual(row["egress_country"], "PT")
        self.assertEqual(row["proxy_ports"], [10001])

    def test_dash_ip_is_ignored_and_falls_back_to_egress_ip_field(self):
        row = enrich(
            {},
            {
                "logs": [
                    "[多径中继网关] 出口拓扑对齐: IP=- 国家=葡萄牙",
                    "egress_ip=198.51.100.22",
                ]
            },
        )
        self.assertEqual(row["egress_ip"], "198.51.100.22")
        self.assertEqual(row["egress_country"], "葡萄牙")

    def test_credential_log_flashcall_flags(self):
        row = enrich(
            {},
            {
                "logs": [
                    "sendCode 凭证核对: api_id=6 attach_token=否 "
                    "firebase=是 unknown=否 flashcall=是 missed=是 app_sandbox=None",
                ]
            },
        )
        self.assertEqual(row["unknown_number"], "否")
        self.assertEqual(row["flashcall"], "是")
        self.assertEqual(row["missed_call"], "是")
