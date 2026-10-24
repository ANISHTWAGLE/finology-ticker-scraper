from datetime import datetime
import json
from pathlib import Path

import pytest

from finology_scraper.retrieval import Snapshot


@pytest.fixture
def captured_tcs():
    root = Path(__file__).parent / "fixtures"
    metadata = json.loads((root / "tcs-capture.json").read_text(encoding="utf-8"))
    return Snapshot(html=(root / "tcs-public.html").read_text(encoding="utf-8"),
        source_url=metadata["source_url"], scraped_at=datetime.fromisoformat(metadata["scraped_at"]),
        chart_text=(root / "tcs-chart.json").read_text(encoding="utf-8"),
        chart_url=metadata["chart_url"], chart_at=datetime.fromisoformat(metadata["chart_at"]),
        legends=metadata["chart_legends"])
