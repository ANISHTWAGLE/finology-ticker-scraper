"""Compare full extraction completeness and time without writing financial snapshots."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from functools import partial
import json
import time

from .batch import execute_item
from .retrieval import Retriever


def benchmark(symbols, basis, engines, concurrency_levels):
    results = []
    for concurrency in concurrency_levels:
        for engine in engines:
            start = time.monotonic()
            factory = partial(Retriever, browser_backend='lightpanda' if engine == 'lightpanda' else 'chrome',
                              browser_only=engine == 'lightpanda', http_fallback=False)
            def run(symbol):
                item_start = time.monotonic()
                item = execute_item(symbol, basis, retriever_factory=factory,
                                    save=lambda result: 'not_saved_benchmark')
                item['seconds'] = round(time.monotonic()-item_start, 3)
                return item
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                items = list(pool.map(run, symbols))
            result = {'engine':engine,'concurrency':concurrency,'seconds':round(time.monotonic()-start,3),
                      'symbols':symbols,'reporting_basis':basis,'items':items}
            results.append(result)
            print(json.dumps(result),flush=True)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--symbols',nargs='+',required=True)
    parser.add_argument('--basis',choices=['standalone','consolidated'],required=True)
    parser.add_argument('--engines',nargs='+',choices=['http','lightpanda'],default=['http','lightpanda'])
    parser.add_argument('--concurrency',nargs='+',type=int,choices=range(1,5),default=[1,2])
    args = parser.parse_args()
    benchmark(args.symbols,args.basis,args.engines,args.concurrency)


if __name__ == '__main__':
    main()
