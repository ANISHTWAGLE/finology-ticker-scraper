# Live transport comparison

For the subsequent registration check and live PostgreSQL results, see the
[Nifty 50 batch notes](../nifty50-batches.md#live-public-data-check). The measurements below
describe the scraper as tested on 6 November.

Measured 6 November 2026 against Screener, with the current `screener_scraper.service.scrape` parser. The sample was TCS and ITC, each in standalone and consolidated form. Every transport ran the same four company-basis items serially, with a shared 2-second request-start gate and one `SharedInvestorDetails` cache per company across its two bases. No database writes were made.

## Results

| Transport | Requests | HTTP 200 / 403 / 429 | Elapsed | Extracted items | Normalized matches to fresh urllib |
| --- | ---: | ---: | ---: | ---: | ---: |
| Production urllib | 86 | 82 / 4 / 0 | 170.53 s | 4 partial | Reference |
| curl_cffi 0.17.0 | 86 | 82 / 4 / 0 | 171.93 s | 4 partial | 4/4 |
| Scrapling 0.5.1 `Fetcher` | 86 | 82 / 4 / 0 | 170.89 s | 4 partial | 4/4 |
| Scrapy 2.19.0 | 2 adapter attempts | No source status returned | Stopped after 90 s per attempt | 0 | Not comparable |

Elapsed time covers the four extraction items and transport initialization after common Python and application imports; it is not process startup-to-finish. The request-start gate accounts for about 170 seconds per 86-request run. curl_cffi and Scrapling did not reduce elapsed time in this paced sample. All three completed transports returned the same normalized extraction digest for each company and basis. The digest omits only `scraped_at` and `retrieval_method`; it covers the rest of the serialized result. An independent cell comparison also found 23,193/23,193 common normalized cells equal across the four items.

Every completed result was `partial`. Each transport received four HTTP 403 responses, one for the nested `Other Expenses %` detail in each company-basis result. No 429 response occurred. The 403 remains unresolved; this comparison does not claim complete data or a 403 fix.

Scrapling's sampled peak process-tree RSS was 143.88 MiB. The direct urllib/curl runner did not sample process-tree RSS during its run, so their peak memory is unavailable here. The older local replay measurements in [large-scale-report.md](large-scale-report.md) used captured responses and are not a live RSS comparison. This evidence cannot establish which completed transport is lightest by memory.

## Recommendation

Keep the current urllib implementation. In this sample it returned the same data and the same 403s as curl_cffi and Scrapling, at effectively the same time under the conservative source gate. Switching transports would add dependency or integration cost without a measured reliability, completeness, or throughput gain. The source gate, not client speed, sets throughput here.

Scrapy was attempted through a persistent worker and the real production parser. Its worker passed a loopback protocol check, but the live company-page fetch did not return a response within 90 seconds. The two parser calls ended as adapter timeouts, so they do not establish a source HTTP status or a Scrapy performance result. Scrapy is not recommended based on this attempt.

The final Scrapling run disabled its default browser-like header generation and TLS impersonation (`stealthy_headers=False`, `impersonate=None`). This preserved the observed 403s. An initial Scrapling probe used the library defaults and emitted browser-like headers plus a Google referer; it was stopped after 15 requests when that behavior was noticed and excluded from the comparison. A separate safe-header run initially passed Scrapling's parsed `html_content` instead of its raw response body to the parser; that four-item result is invalidated and excluded. A paced endpoint probe confirmed the raw response was HTTP 200 JSON; the corrected run used `response.body` and passed all four fresh-reference digest comparisons.

## Scope and limits

- The comparison covers two representative companies, not all Nifty companies or all issuers.
- The production parser and shared investor-request cache were used. Standalone and consolidated requests were run sequentially per company.
- Redirects were disabled for curl_cffi, Scrapling and Scrapy; urllib retained the production same-host redirect guard. The parser checked returned company identity and requested basis.
- No login, paid content, proxy rotation, or database persistence was used. Request pacing remained 2 seconds, and the runs stopped on a 429 cooldown if one appeared.
- This did not repeat the browser comparison. See [the earlier HTTP versus Lightpanda report](2026-11-04-http-lightpanda.md); its small captured cohort was partial and is not evidence that every browser approach is slower.
- Request-by-request observations and normalized item summaries are in [live-transport-comparison-observations.json](live-transport-comparison-observations.json). Full parser outputs and experimental adapter logs remain under ignored `tmp/large-scale/`.
