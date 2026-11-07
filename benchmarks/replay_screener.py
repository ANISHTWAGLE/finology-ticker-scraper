"""Compare full extraction on captured HTTP responses served only on localhost."""

import time
IMPORT_STARTED = time.perf_counter()
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import socket
from pathlib import Path
import subprocess
import sys
from threading import Thread, Lock
from urllib.parse import quote, parse_qs, urlsplit
from urllib.request import build_opener
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'tmp/transport-research'))
import psutil


def client(args):
    from screener_scraper.models import ScreenerRequest
    from screener_scraper.retrieval import SourceError, check_url
    from screener_scraper.service import scrape, SECTION_NAMES
    from screener_scraper.shared_details import SharedInvestorDetails
    from capture_screener import digest
    if args.engine == 'curl':
        from curl_cffi.requests import Session
        factory = partial(Session, timeout=30, allow_redirects=False)
    elif args.engine == 'httpx':
        from httpx import Client
        factory = partial(Client, timeout=30, follow_redirects=False)
    else:
        factory = build_opener
    expected = json.loads(Path(args.expected).read_text())
    jobs = []
    for repeat in range(args.repeats):
        for symbol in expected['symbols']:
            shared = SharedInvestorDetails()
            jobs.extend((symbol,basis,shared) for basis in ('standalone','consolidated'))
    lock = Lock()
    requests = Counter()
    latencies = []
    def run(job):
        symbol,basis,shared = job
        session = factory()
        def fetch(url,**kwargs):
            check_url(url)
            local = args.base+'/fetch?url='+quote(url,safe='')
            if args.engine == 'urllib':
                try:
                    with session.open(local, timeout=30) as response:
                        status,body = response.status,response.read().decode()
                        headers = response.headers
                except HTTPError as exc:
                    status,body = exc.code,exc.read().decode()
                    headers = exc.headers
            else:
                response = session.get(local)
                status,body = response.status_code,response.text
                headers = response.headers
            with lock:
                requests[status] += 1
            if status>=400:
                if status == 403 and headers.get('X-Replay-Registration-Required') == '1':
                    raise SourceError(status, 'Screener requires registration or login for this detail.',
                                      registration_required=True)
                raise SourceError(status,f'Screener returned HTTP {status}.')
            return body,url,'http'
        class Transport:
            def get(self,url,**kwargs):
                return shared.get(fetch,url,**kwargs)
        started = time.perf_counter()
        try:
            result = scrape(ScreenerRequest(nse_symbol=symbol,reporting_basis=basis),Transport(),SECTION_NAMES)
            actual = digest(result)
            return {'symbol':symbol,'basis':basis,'status':result.status,
                    'matches':actual == expected['digests'][symbol+'/'+basis],
                    'seconds':time.perf_counter()-started}
        finally:
            if hasattr(session,'close'):
                session.close()
    startup = time.perf_counter()-IMPORT_STARTED
    started = time.perf_counter()
    cpu_started = sum(psutil.Process().cpu_times()[:2])
    with ThreadPoolExecutor(args.workers) as pool:
        results = list(pool.map(run,jobs))
    elapsed = time.perf_counter()-started
    times = sorted(result['seconds'] for result in results)
    print(json.dumps({'engine':args.engine,'workers':args.workers,'items':len(jobs),
        'elapsed_seconds':elapsed,'startup_seconds':startup,
        'cpu_seconds':sum(psutil.Process().cpu_times()[:2])-cpu_started,
        'items_per_second':len(jobs)/elapsed,'p50_item_seconds':times[len(times)//2],
        'p95_item_seconds':times[math.ceil(len(times)*0.95)-1],
        'requests':dict(requests),'statuses':dict(Counter(r['status'] for r in results)),
        'mismatches':[r for r in results if not r['matches']]}),flush=True)


def driver(args):
    archive = json.loads(Path(args.capture).read_text())
    valid = [item for item in archive['items'] if 'result' in item]
    symbols = sorted({item['symbol'] for item in valid})
    expected = {'symbols':symbols,'digests':{i['symbol']+'/'+i['basis']:i['digest'] for i in valid}}
    expected_path = Path(args.output).with_suffix('.expected.json')
    expected_path.write_text(json.dumps(expected))
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        def setup(self):
            super().setup()
            self.connection.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
        def do_GET(self):
            url = parse_qs(urlsplit(self.path).query).get('url',[''])[0]
            response = archive['responses'].get(url)
            status = response['status'] if response else 599
            body = response['body'].encode() if response else b'Missing capture'
            self.send_response(status)
            self.send_header('Content-Length',str(len(body)))
            if response and response.get('registration_required'):
                self.send_header('X-Replay-Registration-Required','1')
            self.send_header('Content-Type','text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()
        def log_message(self,*args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
    Thread(target=server.serve_forever,daemon=True).start()
    runs = []
    try:
        for workers in args.concurrency:
            for engine in args.engines:
                command = [sys.executable,str(Path(__file__).resolve()),'--client','--engine',engine,
                           '--workers',str(workers),'--repeats',str(args.repeats),
                           '--base',f'http://127.0.0.1:{server.server_port}','--expected',str(expected_path)]
                started = time.perf_counter()
                process = subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
                monitored = psutil.Process(process.pid)
                peak = 0
                while process.poll() is None:
                    try:
                        tree = [monitored]+monitored.children(recursive=True)
                        peak = max(peak,sum(p.memory_info().rss for p in tree))
                    except psutil.Error:
                        pass
                    time.sleep(0.05)
                stdout,stderr = process.communicate()
                if process.returncode:
                    result = {'engine':engine,'workers':workers,'error':stderr}
                else:
                    result = json.loads(stdout)
                result.update(peak_process_tree_rss_mib=peak/1024**2,
                              process_wall_seconds=time.perf_counter()-started)
                runs.append(result)
                Path(args.output).write_text(json.dumps(runs,indent=2))
                print(json.dumps(result),flush=True)
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--client',action='store_true')
    parser.add_argument('--engine')
    parser.add_argument('--workers',type=int,default=1)
    parser.add_argument('--base')
    parser.add_argument('--expected')
    parser.add_argument('--capture',default='tmp/large-scale/capture.json')
    parser.add_argument('--output',default='tmp/large-scale/replay.json')
    parser.add_argument('--repeats',type=int,default=10)
    parser.add_argument('--engines',nargs='+',default=['urllib','httpx','curl'])
    parser.add_argument('--concurrency',nargs='+',type=int,default=[1,2,4])
    args = parser.parse_args()
    if args.client:
        client(args)
    else:
        driver(args)
