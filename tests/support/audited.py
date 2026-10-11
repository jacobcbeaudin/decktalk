"""`python -m decktalk`, with every still frame a recording writes again captured anyway every so often.

    python tests/support/audited.py EVERY ARG...

`decktalk.media.stepping.SKIP_AUDIT_EVERY` is a constant every build DeckTalk runs leaves at zero,
so no setting and no variable reaches it. The suites that build a project as a subprocess run the
command line through this file instead, which raises it to EVERY first, so a frame a build wrote
again that a capture would have drawn differently stops that build.
"""

from __future__ import annotations

import sys

from decktalk.cli import main
from decktalk.media import stepping

stepping.SKIP_AUDIT_EVERY = int(sys.argv[1])
raise SystemExit(main(sys.argv[2:]))
