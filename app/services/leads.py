import yaml
from pathlib import Path
from functools import lru_cache
CONFIG_PATH = Path("config/departments.yml")
@lru_cache(maxsize=1)
def get_departments_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)
