import sys

from afterglow.app import AfterglowApp
from afterglow.config import ConfigError, load_config


def main() -> None:
    try:
        config = load_config()
    except ConfigError as error:
        print(error, file=sys.stderr)
        sys.exit(1)

    AfterglowApp(config).run()
