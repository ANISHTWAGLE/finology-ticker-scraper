"""Serve captured HTML + synthetic financial JSON on loopback; sample child RSS."""
import sys,time,json,hashlib,threading,subprocess,os,socket,gzip
from pathlib import Path
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
import psutil
root=Path(__file__).resolve().parents[2]
payloads={'0':gzip.decompress((root/'benchmarks/framework_replay/fixtures/mrf.html.gz').read_bytes()),'1':json.dumps({'Sales':{'Mar 2026':'8,416','TTM':None},'OPM %':{'Mar 2026':'12.5%','TTM':'0%'}}).encode()}
hashes={k:hashlib.sha256(v).hexdigest() for k,v in payloads.items()}
class Server(ThreadingHTTPServer):
 request_queue_size=128
 daemon_threads=True
class Handler(BaseHTTPRequestHandler):
 protocol_version='HTTP/1.1'
 def setup(self):
  super().setup()
  self.connection.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
 def do_GET(self):
  key=self.path.split('?')[0].strip('/')
  if key not in payloads:self.send_error(404);return
  body=payloads[key]
  time.sleep(0.01)
  self.send_response(200)
  self.send_header('Content-Type','text/html; charset=utf-8' if key=='0' else 'application/json')
  self.send_header('Content-Length',str(len(body)))
  self.end_headers()
  self.wfile.write(body)
  self.wfile.flush()
 def log_message(self,*args):pass
server=Server(('127.0.0.1',0),Handler)
threading.Thread(target=server.serve_forever,daemon=True).start()
folder=root/'tmp'/('framework-replay-results'+os.environ.get('FRAMEWORK_RUN_LABEL',''))
folder.mkdir(exist_ok=True)
results=[]
count=int(sys.argv[1]) if len(sys.argv)>1 else 500
try:
 for name in ['scrapy','crawlee','scrapling']:
  for concurrency in [1,4,16]:
   dest=folder/f'{name}-{concurrency}.json'
   dest.unlink(missing_ok=True)
   log=(folder/f'{name}-{concurrency}.log').open('w')
   run_dir=folder/f'{name}-{concurrency}-storage'
   run_dir.mkdir(exist_ok=True)
   env={**os.environ,'CRAWLEE_STORAGE_DIR':str(run_dir),'NO_PROXY':'127.0.0.1,localhost'}
   command=[sys.executable,str(root/'benchmarks/framework_replay/worker.py'),name,f'http://127.0.0.1:{server.server_port}',str(count),str(concurrency),str(dest),json.dumps(hashes)]
   start=time.perf_counter()
   p=subprocess.Popen(command,cwd=run_dir,env=env,stdout=log,stderr=log)
   peak=0
   while p.poll() is None:
    try:
     process=psutil.Process(p.pid)
     rss=sum(x.memory_info().rss for x in [process,*process.children(recursive=True)] if x.is_running())
     peak=max(peak,rss)
    except (psutil.NoSuchProcess,psutil.AccessDenied):pass
    if time.perf_counter()-start>180:p.kill();break
    time.sleep(.02)
   p.wait();log.close()
   result=json.loads(dest.read_text()) if dest.exists() else {'framework':name,'concurrency':concurrency,'error':(folder/f'{name}-{concurrency}.log').read_text()[-2000:]}
   result.update(peak_rss_mib=round(peak/1024**2,2),process_seconds=round(time.perf_counter()-start,3),exit_code=p.returncode)
   results.append(result)
   print(json.dumps(result),flush=True)
finally:
 server.shutdown()
 (folder/'results.json').write_text(json.dumps({'count_per_run':count,'payload_bytes':{k:len(v) for k,v in payloads.items()},'delay_ms':10,'results':results},indent=2))
