"""Keep only the metrics in k6 summary exports (drops setup_data = auth tokens)."""

import json
import sys
from pathlib import Path

for path in map(Path, sys.argv[1:]):
    data = json.loads(path.read_text())
    path.write_text(json.dumps({"metrics": data["metrics"]}, indent=1))
