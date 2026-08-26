"""100x CAIO — the report pipeline.

Six stages, each independently runnable, sharing one local lake:

    x0  gap check     what is possible on the data present, what is blocked
    x1  pull          fetch into the lake; conversation content lands raw first
    x2  metrics       deterministic computation; no model reads anything
    x3  classify      something reads the flagged passages and judges them
    x4  verify        an independent pass, never shown x3's answer
    x5  lock + render a ledger, then a self-rendering HTML report

The seam between the fetch layer and everything above it is the data contract
in references/data_contract.md. Analysis code reads the lake through
`pipeline.lake.lake_read`; nothing above x1 talks to an API.
"""

__version__ = "2.0.0"
