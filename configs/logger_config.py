import logging
from pathlib import Path

from configs.global_config import IS_DEBUG

PROJECT_DIR = Path(__file__).parent.parent
LOG_DIR = PROJECT_DIR
LOG_FILE = LOG_DIR / 'debug.log'

LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.DEBUG if IS_DEBUG else logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler()
    ]
)


def get_logger(name):
    return logging.getLogger(name)
