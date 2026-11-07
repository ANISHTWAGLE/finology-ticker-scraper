"""Local static financial-table browser benchmark; no upstream requests."""

import argparse
import asyncio
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import subprocess
import sys
from threading import Thread
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tmp/transport-research'))
import psutil
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

TABLES_JS = r"""() => Array.from(document.querySelectorAll('table')).map(t =>
    Array.from(t.querySelectorAll('tr')).map(r =>
        Array.from(r.querySelectorAll('th,td')).map(c =>
            c.textContent.replace(/\s+/g, ' ').trim())))"""


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False).encode()).hexdigest()


def prepare_fixture():
    soup = BeautifulSoup((ROOT / 'tmp/screener-mrf.html').read_text(encoding='utf-8'), 'html.parser')
    for element in soup.select('script,style,link,img,iframe,video,audio,source,embed,object'):
        element.decompose()
    for element in soup.find_all():
        for name in list(element.attrs):
            if name.lower().startswith('on'):
                del element.attrs[name]
    tables = [[[' '.join(c.get_text().split()) for c in row.select('th,td')]
               for row in table.select('tr')] for table in soup.select('table')]
    return str(soup).encode(), digest(tables), len(tables), sum(len(t) for t in tables)


async def exercise(args):
    started = time.perf_counter()
    mismatches, failures, timings = [], [], []
    blocked = 0
    async with async_playwright() as playwright:
        chromium = None
        if args.engine == 'chrome' and args.mode == 'reuse':
            chromium = await playwright.chromium.launch(channel='chrome', headless=True)
        startup = time.perf_counter() - started
        async def worker(worker_id):
            nonlocal blocked
            connection = None
            if args.engine == 'lightpanda' and args.mode == 'reuse':
                connection = await playwright.chromium.connect_over_cdp(args.cdp)
            try:
                for index in range(worker_id, args.requests, args.workers):
                    tick = time.perf_counter()
                    browser = chromium or connection
                    context = None
                    try:
                        if browser is None:
                            if args.engine == 'chrome':
                                browser = await playwright.chromium.launch(channel='chrome', headless=True)
                            else:
                                browser = await playwright.chromium.connect_over_cdp(args.cdp)
                        context = await browser.new_context()
                        page = await context.new_page()
                        async def guard(route):
                            nonlocal blocked
                            if route.request.url.startswith(args.base + '/'):
                                await route.continue_()
                            else:
                                blocked += 1
                                await route.abort()
                        await page.route('**/*', guard)
                        response = await page.goto(args.base + '/fixture?n=' + str(index),
                                                   wait_until='domcontentloaded', timeout=15000)
                        actual = digest(await page.evaluate(TABLES_JS))
                        if response.status != 200 or actual != args.expected:
                            mismatches.append({'index':index,'http_status':response.status,'hash':actual})
                    except Exception as exc:
                        failures.append({'index':index,'error':type(exc).__name__,'detail':str(exc)[:300]})
                    finally:
                        if context is not None:
                            await context.close()
                        if args.mode == 'fresh' and browser is not None:
                            await browser.close()
                        timings.append(time.perf_counter()-tick)
            finally:
                if connection is not None:
                    await connection.close()
        execution = time.perf_counter()
        await asyncio.gather(*(worker(index) for index in range(args.workers)))
        elapsed = time.perf_counter()-execution
        if chromium is not None:
            await chromium.close()
    return dict(engine=args.engine,mode=args.mode,workers=args.workers,requests=args.requests,
                seconds=elapsed,startup_seconds=startup,requests_per_second=args.requests/elapsed,
                errors=failures,mismatches=mismatches,blocked_requests=blocked,
                median_request_seconds=sorted(timings)[len(timings)//2])


def main(args):
    body, expected, tables, rows = prepare_fixture()
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        def setup(self):
            super().setup()
            self.connection.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Length',str(len(body)))
            self.send_header('Content-Type','text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()
        def log_message(self,*args):
            pass
    server = ThreadingHTTPServer(('0.0.0.0',0),Handler)
    Thread(target=server.serve_forever,daemon=True).start()
    results = []
    try:
        variants = [(engine,'reuse',workers,args.requests)
                    for workers in args.concurrency for engine in args.engines]
        if args.include_fresh:
            variants += [(engine,'fresh',1,10) for engine in args.engines]
        for engine,mode,workers,count in variants:
            base = f'http://host.docker.internal:{server.server_port}' if engine == 'lightpanda' else f'http://127.0.0.1:{server.server_port}'
            command = [sys.executable,str(Path(__file__).resolve()),'--child','--engine',engine,
                       '--mode',mode,'--workers',str(workers),'--requests',str(count),
                       '--base',base,'--expected',expected,'--cdp',args.cdp]
            process = subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            monitored = psutil.Process(process.pid)
            peak, docker_peak = 0, 0
            next_docker = 0
            while process.poll() is None:
                try:
                    peak = max(peak,sum(p.memory_info().rss for p in [monitored]+monitored.children(recursive=True)))
                except psutil.Error:
                    pass
                if engine == 'lightpanda' and time.monotonic() >= next_docker:
                    stat = subprocess.run(['docker','stats','--no-stream','--format','{{.MemUsage}}',args.container],
                                          capture_output=True,text=True)
                    try:
                        value = stat.stdout.split('/')[0].strip()
                        if value.endswith('MiB'):
                            docker_peak = max(docker_peak,float(value[:-3]))
                        elif value.endswith('GiB'):
                            docker_peak = max(docker_peak,float(value[:-3])*1024)
                    except ValueError:
                        pass
                    next_docker = time.monotonic()+1
                time.sleep(.02)
            stdout,stderr = process.communicate()
            result = json.loads(stdout) if process.returncode == 0 else {'engine':engine,'mode':mode,'workers':workers,'fatal_error':stderr}
            result.update(peak_windows_process_tree_rss_mib=peak/1024**2,
                          sampled_container_memory_peak_mib=docker_peak if engine=='lightpanda' else None)
            results.append(result)
            document = {'fixture_bytes':len(body),'tables':tables,'rows':rows,'expected_hash':expected,'runs':results}
            Path(args.output).parent.mkdir(parents=True,exist_ok=True)
            Path(args.output).write_text(json.dumps(document,indent=2),encoding='utf-8')
            print(json.dumps(result),flush=True)
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--child',action='store_true')
    parser.add_argument('--engine')
    parser.add_argument('--mode',default='reuse')
    parser.add_argument('--workers',type=int,default=1)
    parser.add_argument('--requests',type=int,default=40)
    parser.add_argument('--base')
    parser.add_argument('--expected')
    parser.add_argument('--cdp',default='ws://127.0.0.1:19223')
    parser.add_argument('--container',default='scraper-browser-benchmark')
    parser.add_argument('--engines',nargs='+',default=['chrome','lightpanda'])
    parser.add_argument('--concurrency',nargs='+',type=int,default=[1,2,4])
    parser.add_argument('--include-fresh',action='store_true')
    parser.add_argument('--output',default='docs/benchmarks/browser-replay-results.json')
    args = parser.parse_args()
    if args.child:
        print(json.dumps(asyncio.run(exercise(args))),flush=True)
    else:
        main(args)
