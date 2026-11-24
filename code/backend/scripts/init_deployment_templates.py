"""种部署模板；幂等，不覆盖已有配置。"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.config import load_dotenv, load_settings  # noqa: E402
from app.services import deployment_service  # noqa: E402


def main() -> int:
    load_dotenv()
    settings = load_settings()
    inserted = deployment_service.seed_templates(settings)
    print(f"templates_inserted={inserted}")
    print("templates=" + ",".join(deployment_service.SEED_TEMPLATES))
    return 0


if __name__ == "__main__":
    sys.exit(main())
