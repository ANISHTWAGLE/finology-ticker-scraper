# Live Screener comparison: 4 November 2026

Recommendation for this implementation: **HTTP, one worker**. Lightpanda was
slightly slower and had additional retrieval failures. Two workers did not
improve elapsed time while preserving the shared two-second source spacing.

These are full live extraction attempts for **TCS and Infosys, consolidated**,
including public row-expansion requests. They are not mocked runs or
browser-assisted inspection. They ran through the service's Python extraction
functions, rather than through a deployed batch HTTP request. They did not save
financial data.

| Engine | Workers | Total time, two companies | TCS rows | Infosys rows | Result |
| --- | ---: | ---: | ---: | ---: | --- |
| HTTP | 1 | 106.250 s | 805 | 233 | Both partial |
| Lightpanda | 1 | 108.922 s | 805 | 222 | Both partial |
| HTTP | 2 | 107.219 s | 805 | 233 | Both partial |
| Lightpanda | 2 | 108.844 s | 803 | 216 | Both partial |

Both engines received **HTTP 403** for a nested `Other Expenses %` detail under
profit & loss. This remains an explicit partial extraction. The response does
not establish whether the source intends paid/login access or whether its access
policy blocked the request, so it is not silently excluded from completeness.

Lightpanda additionally failed to retrieve some individual details: Fixed Assets
and Government shareholding in the one-worker Infosys run; Other Income for TCS
and Net Profit/Fixed Assets for Infosys in the two-worker run. Successful page
loading did not imply successful full extraction. See the
[raw observations](2026-11-04-http-lightpanda.json) for each section's status,
row counts, issues and timing.

Settings ran sequentially: HTTP/1, Lightpanda/1, HTTP/2, Lightpanda/2. Network
conditions, browser startup, source/cache state and the source limiter all
contribute to elapsed time. This is one small comparison, not a statistical
benchmark, a hardware-efficiency measurement, or proof about all Nifty 50
companies. Neither CPU nor memory usage was measured. Both reporting bases are
covered by the batch implementation and database tests; this live comparison
used consolidated data only. No full live 100-item Nifty 50 batch was executed.

Lightpanda image digest:
`sha256:5d1d724c476957d8c2f8393a51fd089477523b0e5aa9c783c48688785d934fb9`.
The temporary comparison container was stopped afterward.

The same investigation found two source-format changes which were corrected
before these runs: the Sales growth toggle reveals rows already present in HTML,
and detail JSON now includes `tooltips` metadata. Regression tests preserve the
growth rows and distinguish this metadata from reporting-period cells.
