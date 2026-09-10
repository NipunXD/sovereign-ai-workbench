"""The static pre-check.

Not the security boundary — the container is. This is the fast, legible refusal
that tells an agent *why* its code was rejected, before paying for a container
start and surfacing a confusing runtime error instead.
"""

from __future__ import annotations

import pytest

from workbench.sandbox.static_check import check


@pytest.mark.parametrize(
    "code",
    [
        "import numpy as np\nprint(np.mean([1,2,3]))",
        "import pandas as pd\ndf = pd.DataFrame({'a':[1]})",
        "import matplotlib.pyplot as plt\nplt.savefig('out.png')",
        "import os\nprint(os.path.join('a','b'))",
        "import math, json\nprint(math.sqrt(2))",
        "with open('result.txt','w') as f:\n    f.write('done')",
    ],
)
def test_legitimate_engineering_code_is_allowed(code: str) -> None:
    """The check must not obstruct the work the sandbox exists for."""
    assert check(code).ok, check(code).summary


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("import socket", "socket"),
        ("import requests", "requests"),
        ("from urllib.request import urlopen", "urllib"),
        ("import httpx", "httpx"),
        ("import subprocess", "subprocess"),
        ("import ctypes", "ctypes"),
        ("import multiprocessing", "multiprocessing"),
    ],
)
def test_network_and_process_modules_are_refused(code: str, expected: str) -> None:
    result = check(code)
    assert not result.ok
    assert expected in result.summary


@pytest.mark.parametrize(
    "code",
    [
        "eval('1+1')",
        "exec('x=1')",
        "__import__('socket')",
        "import os\nos.system('ls')",
        "import os\nos.fork()",
        "().__class__.__bases__[0].__subclasses__()",
    ],
)
def test_escape_routes_are_refused(code: str) -> None:
    assert not check(code).ok


def test_syntax_errors_are_reported_not_raised() -> None:
    """A model emitting broken code should get a message, not a crash."""
    result = check("def broken(:")
    assert not result.ok
    assert "syntax error" in result.summary


def test_violations_name_the_line() -> None:
    result = check("import numpy\nimport socket\nprint(1)")
    assert "line 2" in result.summary


def test_allowed_imports_are_recorded() -> None:
    """Recorded on the audit trail: what the code asked for, not just whether
    it was allowed."""
    result = check("import numpy\nimport pandas as pd\nfrom math import sqrt")
    assert set(result.imports) == {"numpy", "pandas", "math"}
