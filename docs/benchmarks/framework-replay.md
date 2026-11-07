# Local framework replay results

Measured on 6 November 2026, Windows x86-64 and Python 3.12. The tested framework versions were Scrapy 2.19.0, Crawlee 1.11.1 and Scrapling 0.5.1.

Scrapy had the highest local request throughput in this matrix. Scrapling's asynchronous FetcherSession used the least sampled process memory. These measurements support selecting candidates for a real workload comparison. They do not establish a safe request rate for Screener.

| Framework path | Concurrency | Verified responses | Crawl seconds | Responses/s | Sampled peak RSS, MiB |
| --- | ---: | ---: | ---: | ---: | ---: |
| Scrapy Spider | 1 | 500/500 | 12.955 | 38.6 | 83.45 |
| Scrapy Spider | 4 | 500/500 | 3.566 | 140.2 | 83.66 |
| Scrapy Spider | 16 | 500/500 | 1.365 | 366.2 | 88.97 |
| Crawlee HttpCrawler | 1 | 500/500 | 20.173 | 24.8 | 94.20 |
| Crawlee HttpCrawler | 4 | 500/500 | 10.378 | 48.2 | 97.67 |
| Crawlee HttpCrawler | 16 | 500/500 | 9.864 | 50.7 | 98.52 |
| Scrapling FetcherSession | 1 | 500/500 | 19.715 | 25.4 | 59.83 |
| Scrapling FetcherSession | 4 | 500/500 | 4.978 | 100.4 | 60.02 |
| Scrapling FetcherSession | 16 | 500/500 | 2.323 | 215.2 | 60.38 |

All 4,500 measured responses matched the expected SHA-256 of the response body. All processes exited successfully. Each framework/concurrency combination ran once, after a separate 20-request smoke run. Raw measurements, including total process times and framework import/setup times, are in [framework-replay.json](framework-replay.json).

## Workload and measurement

The loopback server alternated two bodies across 500 unique URLs. Half were a previously captured 192,810-byte public MRF HTML page. Half were a 90-byte synthetic financial JSON response containing a number with a comma, a missing value, a percentage and zero. Every handler waited 10 milliseconds before responding. Responses used HTTP/1.1 with Content-Length, TCP_NODELAY and explicit flushing. The server allowed a backlog of 128 connections.

The HTML payload is archived in `benchmarks/framework_replay/fixtures/mrf.html.gz` so reruns use the same bytes. Its SHA-256 is recorded in the JSON report. No JavaScript or external page resources are loaded. All benchmark request URLs point to loopback.

Each run used a fresh worker process. The parent sampled the worker and its descendants every 20 milliseconds and recorded their summed resident memory. The replay server and controller memory are excluded. Sampling can miss short memory peaks. Package installation size was not measured. Crawl timing begins immediately before the framework's run/fetch operation. Total process timing additionally includes Python startup, framework imports and configuration.

The handlers perform exact response-body verification. Production financial-table parsing and PostgreSQL persistence are excluded. Scrapy and Crawlee use their crawl scheduling machinery. Scrapling uses its bounded asynchronous fetch session, rather than a Spider with queue persistence. Default internal parsing/session/queue overhead can therefore differ. This comparison measures the particular configured entry points, not interchangeable end-to-end crawler implementations.

Crawlee used its default Impit client and request queue behavior. Its concurrency minimum, desired and maximum were all pinned to the tested value. Scrapy pinned overall and per-domain concurrency to the tested value, with no download delay. Scrapling limited active fetches with an asyncio semaphore. Automatic retries and redirects were disabled in the test paths where explicitly configured. All source responses were 200, so the matrix does not measure retry recovery, 403/429 behavior, TLS negotiation, browser rendering or remote network variability.

## Settings supported by this test

For a high-volume, low-latency local HTTP workload, Scrapy concurrency 16 achieved the best tested throughput, with a sampled peak below 90 MiB. For a small asynchronous fetching layer with lower process memory, Scrapling FetcherSession concurrency 16 was a viable alternative at about 60 MiB. Crawlee offers orchestration features, but its default HttpCrawler path had more overhead in this workload.

Live Screener settings must come from the separate paced upstream tests. This local benchmark has no source-enforced rate limit. Increasing live concurrency to 16 solely from these results would not be justified.

## Reproduce

Run from the repository root in PowerShell. The lock file records the complete isolated test environment.

```powershell
python -m venv tmp/framework-env
.\tmp\framework-env\Scripts\python.exe -m pip install -r benchmarks/framework_replay/requirements-lock.txt
.\tmp\framework-env\Scripts\python.exe benchmarks/framework_replay/run.py 500
```

The driver writes logs and machine-readable results under `tmp/framework-replay-results`. Set `FRAMEWORK_RUN_LABEL` to a suffix to keep another run in a separate directory. Run one performance matrix at a time and close unrelated CPU-intensive processes for a more controlled repeat. Results in this report came from a single shared Windows host and should be repeated before treating small differences as stable.

Official API references used during setup are the [Crawlee quick start](https://crawlee.dev/python/docs/quick-start), [Scrapy programmatic execution documentation](https://docs.scrapy.org/en/latest/topics/practices.html), and the installed Scrapling 0.5.1 `FetcherSession` implementation under `scrapling/engines/static.py`. The complete project is documented in the [Scrapling repository](https://github.com/D4Vinci/Scrapling).
