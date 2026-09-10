"""Print the API's OpenAPI schema to stdout.

Used by `make types-gen` to regenerate the TypeScript client, so it must not
write anything else to stdout — log lines in the middle of the JSON would
produce a package that fails to parse rather than one that is merely stale.
"""

from __future__ import annotations

import json
import logging
import os
import sys


def main() -> int:
    # Importing the app configures structured logging, which writes to stdout by
    # default. Silence it before the import rather than after.
    os.environ.setdefault("WORKBENCH_LOG_LEVEL", "CRITICAL")
    logging.disable(logging.CRITICAL)

    from workbench.main import create_app

    app = create_app()
    json.dump(app.openapi(), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
