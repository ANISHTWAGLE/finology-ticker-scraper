# Scraping benchmark: live capture and local replay

Measured on 6 November 2026. The strongest current production recommendation is to keep the existing paced HTTP implementation. The live comparison is too small and every result is partial; the faster local alternatives have not established a safe live rate or complete extraction. In particular, a local throughput result does not justify increasing traffic to Screener.

The follow-up live comparison of urllib, curl_cffi, Scrapy and Scrapling is documented in [live-transport-comparison.md](live-transport-comparison.md). It used two companies and both reporting bases per completed transport; all results remained partial.

## Live capture: five companies, both bases

A direct HTTP extraction captured TCS, Infosys, Reliance, HDFC Bank and ITC, each in standalone and consolidated form. It ran each pair with two workers sharing a two-second request-start gate and shared investor-detail retrieval. Pairs ran one after another. This used the Python extraction functions directly, not the deployed batch HTTP API or PostgreSQL. The captured financial data was not persisted to the database.

| Company | Requests | Pair elapsed seconds | Standalone / consolidated |
| --- | ---: | ---: | --- |
| TCS | 44 | 86.396 | Partial / partial |
| Infosys | 42 | 83.871 | Partial / partial |
| Reliance | 42 | 83.773 | Partial / partial |
| HDFC Bank | 37 | 73.794 | Partial / partial |
| ITC | 42 | 83.648 | Partial / partial |

Across 207 requests, 197 returned 200 and 10 returned 403. Each result was partial: its nested `Other Expenses %` profit-and-loss detail returned 403. The response does not establish why access was denied. There were no 429 responses in this capture. Summed pair times were 411.482 seconds, approximately 1.46 company-basis results per minute for this particular sample and configuration. The request gate makes network pacing dominant; this is neither a guarantee for all companies nor a safe source-wide rate. These are live extraction attempts, not ten complete successful scrapes.

The earlier [live HTTP versus Lightpanda comparison](2026-11-04-http-lightpanda.md) covered TCS and Infosys consolidated with one and two workers. HTTP had fewer retrieval failures and two workers did not reduce elapsed time under the shared spacing. Its runs were also partial and did not persist data. Together, these observations support retaining HTTP and conservative pacing; they do not prove browser-based approaches are universally slower or that a larger live batch will succeed.

## Full-parser local replay

The captured responses were replayed over loopback, including the original 403s, to the current financial extraction code. Each measured run processed 20 company-basis items: the ten captured combinations repeated twice. This is five unique companies, not a live 20-company scrape. The runs used the same saved input and compared a SHA-256 digest of the complete JSON result after excluding `scraped_at` and `retrieval_method`. Thus a match includes statuses, values and other serialized fields but is agreement with the captured parser output, not independent proof of source completeness or financial correctness. Every result remained partial. PostgreSQL, remote network conditions, source throttling, TLS and browser rendering were outside this test.

| Local transport | Workers | Items/s | Elapsed s | p50 / p95 item s | Peak sampled worker-tree RSS MiB | Matched |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| urllib | 1 | 1.72 | 11.64 | 0.51 / 1.05 | 74.30 | 20/20 |
| httpx | 1 | 1.22 | 16.40 | 0.33 / 0.49 | 79.68 | 20/20 |
| curl_cffi | 1 | 2.60 | 7.71 | 0.36 / 0.85 | 76.26 | 20/20 |

These runs were executed serially after stopping overlapping benchmark processes, once per transport on a shared host; unrelated activity was not controlled. The local server and controller memory are excluded, and 50 ms sampling may miss peaks. Each run made 394 local 200 responses and 20 local 403 responses. `httpx` timings include its per-item client setup; the measurement does not establish that HTTPX itself is intrinsically slower under a different client lifecycle. `curl_cffi` was fastest in this small test, but it has not been tested against the live source in a matched full-parser comparison, so replacing the current transport is not justified by this replay alone. Raw measurements are in `tmp/large-scale/replay-controlled.json`; the capture in `tmp/large-scale/capture.json` contains source responses and extracted data and is local temporary material. An earlier larger matrix overlapped another benchmark process and is exploratory only; it is excluded from the timing comparison above.

## Framework comparison

The separate [framework replay](framework-replay.md) measured framework request scheduling and exact response-body hashes, not this application's financial parser. On 500 loopback responses at concurrency 16, Scrapy reached 366.2 responses/s at 88.97 MiB sampled peak RSS, Scrapling FetcherSession reached 215.2 responses/s at 60.38 MiB, and Crawlee HttpCrawler reached 50.7 responses/s at 98.52 MiB. All responses matched their body hashes. Different framework paths have different scheduling and queue overhead, and each setting ran only once. These results are useful for choosing candidates for further tests, not evidence for a framework migration or live source concurrency.

## Remaining evidence

A full Nifty 50 run, all 100 basis combinations, database persistence under actual batch execution, complete source access, and a matched live transport or browser comparison at larger scale remain unverified. Stop or cool down on 429 according to the service's rate-limit handling. Investigate the recurring 403 without bypassing access controls; retain partial status until the missing detail can be retrieved legitimately. Repeat local timings under controlled conditions before relying on small differences, then evaluate any candidate against the current implementation under the same permitted live pacing and completeness checks.
