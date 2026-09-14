#!/usr/bin/env python3
"""The one place that imports `requests`, so its absence is a sentence.

`requests` is the only dependency the fetch layer needs, and downloading is the
only thing in this project that needs it — the demo, the gap check, the scan,
the metrics, the render and every publication gate are standard library, end to
end. That is a deliberate property: the parts that have to run in a locked-down
environment have nothing to install.

The cost of that property is where it lands. Somebody installs the plugin, looks
at the example reports, and everything works; the first thing they ever do that
needs a third-party library is their first real pull, several steps in, on the
command that finally touches their own account. Before this module, that arrived
as a bare `ModuleNotFoundError` traceback out of an import three frames down —
which reads like the tool is broken, not like it is one `pip install` short.

So both network modules import `requests` through here, rather than directly,
and a missing one gets a message that says what is missing, why it did not come
up until now, and the exact command to fix it. `sys.executable` is in that
command on purpose: a plugin install and a `pip` on PATH are routinely different
interpreters, and "pip install requests" that installs into the wrong one is a
worse failure than the traceback, because it looks like it worked.
"""

from __future__ import annotations

import sys
from types import ModuleType

__all__ = ["require_requests"]

_MESSAGE = """\
Downloading needs the 'requests' library, and it is not installed for this
Python ({exe}).

Nothing else in this project needs it. The example reports, the gap check, the
scan and the report gates are all standard library, which is why this is the
first time it has come up.

Install it:

    {exe} -m pip install requests

or, from a checkout of this repository:

    {exe} -m pip install -r requirements.txt

Then run the same command again. Nothing was written, and nothing is
half-finished — a pull writes a week whole or not at all.\
"""


def require_requests() -> ModuleType:
    """Return the `requests` module, or explain its absence and exit 1.

    Exits with a plain integer code rather than a message-carrying SystemExit:
    `cli._run_fetch` turns a fetch module's SystemExit back into a return code,
    and a string payload there would fail the conversion instead of reporting
    the problem. The message is printed here so it reads the same whether the
    caller was `caio pull`, `python3 -m pipeline.fetch.analytics`, or an import
    from somebody's own script.
    """
    try:
        import requests
    except ModuleNotFoundError as exc:
        if exc.name != "requests":  # something requests itself depends on
            raise
        print(_MESSAGE.format(exe=sys.executable), file=sys.stderr)
        raise SystemExit(1) from None
    return requests
