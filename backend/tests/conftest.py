"""现有 TestClient 测试默认关闭控制台鉴权；test_auth.py 会覆盖为启用。"""
import os

os.environ.setdefault("EDGENODE_AUTH_DISABLED", "1")
os.environ.setdefault("EDGENODE_SKIP_PUSH_REFUND_WAIT", "1")
