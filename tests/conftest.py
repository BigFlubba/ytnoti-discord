import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

CHANNEL = "UCuFFtHWoLl5fauMMD5Ww2jA"
HOOK = "https://discord.com/api/webhooks/123456789/abcDEF_ghi-123"
HOOK2 = "https://discord.com/api/webhooks/987654321/zzzYYY_xxx-456"

BASE = f"""
server:
  callback_url: https://yt.example.com/ytnoti
webhooks:
  main: {HOOK}
channels:
  - id: {CHANNEL}
    name: CBC News
"""


@pytest.fixture
def write_cfg(tmp_path):
    def _write(text: str = BASE):
        p = tmp_path / "config.yaml"
        p.write_text(text, encoding="utf-8")
        return p
    return _write
