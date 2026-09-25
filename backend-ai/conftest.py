import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
os.environ.setdefault("SESSION_TOKEN", "test")   # config.py refuses an empty token
