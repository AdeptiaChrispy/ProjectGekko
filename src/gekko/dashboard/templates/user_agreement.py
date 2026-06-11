"""User Agreement text — REG-02.

Shown verbatim by ``gekko init`` (CLI) and by the dashboard's
``/agreement`` route (Plan 01-09 future surface). The exact wording is
load-bearing for REG-02 compliance — re-wording requires a docs review
and a new ``agreement_acknowledged_at`` timestamp column migration if
the prior text needs to be re-acknowledged.
"""

from __future__ import annotations

USER_AGREEMENT_TEXT: str = """\
Gekko User Agreement

1. Gekko is personal trade-execution tooling acting on YOUR own authored
   strategy. The strategy, the trade decisions, and the resulting tax
   and financial consequences are entirely yours.

2. Gekko is NOT investment advice. Strategies and trades are your own
   decisions; Gekko executes them on your behalf within the hard caps
   you author.

3. You acknowledge that you understand the risks of automated trading,
   including losing money. You will start with paper trading
   (the default in Phase 1).

4. Each user runs their own isolated Gekko instance on their own
   hardware (REG-03). Gekko does not share data across users.

5. You agree to keep your SQLCipher passphrase secret. If you lose it,
   your encrypted database is unrecoverable.

Type "I agree" to acknowledge. Type anything else to abort.
"""

__all__: tuple[str, ...] = ("USER_AGREEMENT_TEXT",)
