"""Bounded live source capture for paired-basis scraper benchmarking."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from threading import Lock
import time
from urllib.error import HTTPError

from screener_scraper.models import ScreenerRequest
from screener_scraper.pacing import RequestGate
from screener_scraper.retrieval import Retriever, SourceError, registration_redirect
from screener_scraper.service import scrape, SECTION_NAMES
from screener_scraper.shared_details import SharedInvestorDetails


def digest(result):
    data = result.model_dump(mode='json')
    data.pop('scraped_at')
    data.pop('retrieval_method')
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--symbols', nargs='+', default=['TCS','INFY','RELIANCE','HDFCBANK','ITC'])
    parser.add_argument('--output', default='tmp/large-scale/capture.json')
    args = parser.parse_args()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    gate, lock = RequestGate(interval=2), Lock()
    archive = {'captured_at':datetime.now(timezone.utc).isoformat(), 'interval':2,
               'responses':{}, 'requests':[], 'items':[]}

    class CapturingRetriever(Retriever):
        def _fetch(self, url, *, browser_fallback):
            gate.acquire()
            start = time.perf_counter()
            headers = {}
            try:
                with self.opener.open(url, timeout=30) as response:
                    status, final_url = response.status, response.url
                    body = response.read().decode('utf-8')
                    headers = response.headers
            except HTTPError as exc:
                status, final_url = exc.code, exc.url
                body = exc.read().decode('utf-8', errors='replace')
                headers = exc.headers
            except OSError as exc:
                raise SourceError(504, 'Screener HTTP request failed or timed out.') from exc
            elapsed = time.perf_counter()-start
            with lock:
                archive['responses'][url] = {'status':status,'body':body,'url':final_url}
                if registration_redirect(status, headers):
                    archive['responses'][url]['registration_required'] = True
                archive['requests'].append({'url':url,'status':status,'seconds':elapsed})
            if status == 429:
                retry = headers.get('Retry-After', '60')
                gate.cooldown(int(retry) if retry.isdigit() else 60)
            if status >= 400:
                if registration_redirect(status, headers):
                    raise SourceError(status, 'Screener requires registration or login for this detail.',
                                      registration_required=True)
                raise SourceError(status, f'Screener returned HTTP {status}.')
            return body, final_url, 'http'

    for symbol in args.symbols:
        shared = SharedInvestorDetails()
        before = len(archive['requests'])
        start = time.perf_counter()
        def worker(basis):
            retriever = CapturingRetriever(gate=gate, shared_investors=shared, http_fallback=False)
            item = {'symbol':symbol,'basis':basis}
            try:
                result = scrape(ScreenerRequest(nse_symbol=symbol,reporting_basis=basis),retriever,SECTION_NAMES)
                item.update(result=result.model_dump(mode='json'), digest=digest(result))
            except SourceError as exc:
                item.update(error={'status':exc.status,'message':str(exc)})
            return item
        with ThreadPoolExecutor(2) as pool:
            items = list(pool.map(worker,['standalone','consolidated']))
        archive['items'].extend(items)
        summary = {'symbol':symbol,'seconds':round(time.perf_counter()-start,3),
                   'requests':len(archive['requests'])-before,
                   'statuses':[item.get('result',{}).get('status',item.get('error')) for item in items]}
        archive.setdefault('pairs',[]).append(summary)
        output.write_text(json.dumps(archive,indent=2),encoding='utf-8')
        print(json.dumps(summary),flush=True)
        if time.monotonic() < gate.blocked_until:
            print('429 cooldown active; stopping capture.',flush=True)
            break


if __name__ == '__main__':
    main()
