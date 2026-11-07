"""Local workload child. No external URLs are accepted."""
import sys,time,json,hashlib,asyncio,logging,importlib.metadata
started=time.perf_counter()
name,base,count,concurrency,out,hashes=sys.argv[1:]
assert base.startswith('http://127.0.0.1:')
count,concurrency=int(count),int(concurrency)
expected=json.loads(hashes)
urls=[f'{base}/{i%2}?id={i}' for i in range(count)]
good=bad=0
def verify(body,url):
 global good,bad
 key=url.split('?')[0].rsplit('/',1)[1]
 if hashlib.sha256(body).hexdigest()==expected[key]:good+=1
 else:bad+=1
logging.disable(logging.CRITICAL)
crawl_start=None
if name=='scrapy':
 import scrapy
 from scrapy.crawler import CrawlerProcess
 class Bench(scrapy.Spider):
  name='localbench'
  async def start(self):
   for url in urls:yield scrapy.Request(url,callback=self.parse)
  def parse(self,response):
   verify(response.body,response.url)
 process=CrawlerProcess(settings={'LOG_ENABLED':False,'ROBOTSTXT_OBEY':False,
  'CONCURRENT_REQUESTS':concurrency,'CONCURRENT_REQUESTS_PER_DOMAIN':concurrency,
  'DOWNLOAD_DELAY':0,'RETRY_ENABLED':False,'TELNETCONSOLE_ENABLED':False,
  'REDIRECT_ENABLED':False})
 process.crawl(Bench)
 crawl_start=time.perf_counter()
 process.start()
elif name=='crawlee':
 from crawlee.crawlers import HttpCrawler
 from crawlee import ConcurrencySettings
 async def run():
  global crawl_start
  crawler=HttpCrawler(concurrency_settings=ConcurrencySettings(min_concurrency=concurrency,max_concurrency=concurrency,desired_concurrency=concurrency),max_request_retries=0)
  @crawler.router.default_handler
  async def handle(context):
   verify(await context.http_response.read(),context.request.url)
  crawl_start=time.perf_counter()
  await crawler.run(urls)
 asyncio.run(run())
elif name=='scrapling':
 from scrapling.fetchers import FetcherSession
 async def run():
  global crawl_start
  semaphore=asyncio.Semaphore(concurrency)
  async with FetcherSession(retries=0,follow_redirects=False) as session:
   async def fetch(url):
    async with semaphore:
     response=await session.get(url)
     verify(response.body,url)
   crawl_start=time.perf_counter()
   await asyncio.gather(*(fetch(u) for u in urls))
 asyncio.run(run())
result=dict(framework=name,version=importlib.metadata.version(name),concurrency=concurrency,count=count,
 verified=good,mismatched=bad,missing=count-good-bad,seconds=time.perf_counter()-crawl_start,
 total_seconds=time.perf_counter()-started)
from pathlib import Path
Path(out).write_text(json.dumps(result,indent=2))
