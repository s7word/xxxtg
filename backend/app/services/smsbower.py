"""SMS Bower (smsbower.app) 接码客户端。

协议与 SMS-Activate / Grizzly SMS 兼容：
  GET https://smsbower.page/stubs/handler_api.php
    ?api_key={key}&action={getBalance|getPrices|getNumber|getStatus|setStatus}

国家数字 ID 与 SMS-Activate 一致，直接复用 resolve_grizzly_country_id。
"""
from __future__ import annotations

from backend.app.services.grizzlysms import GrizzlySmsService


class SmsBowerService(GrizzlySmsService):
    BASE_URL = "https://smsbower.page/stubs/handler_api.php"
    PROVIDER_NAME = "smsbower"
    PROVIDER_LABEL = "SMS Bower (smsbower.app)"


PROVIDER_NAME = SmsBowerService.PROVIDER_NAME
PROVIDER_LABEL = SmsBowerService.PROVIDER_LABEL
