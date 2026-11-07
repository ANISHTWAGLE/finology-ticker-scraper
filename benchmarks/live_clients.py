"""Small paced live-client comparison using URLs from the captured test cohort."""

import json
import hashlib
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'tmp/transport-research'))
from bs4 import BeautifulSoup
from curl_cffi.requests import Session
from httpx import Client
from screener_scraper.pacing import RequestGate
from screener_scraper.retrieval import check_url


def main():
    archive = json.loads((ROOT/'tmp/large-scale/capture.json').read_text())
    selected = []
    for symbol in ['TCS','INFY','RELIANCE','HDFCBANK','ITC']:
        page = f'https://www.screener.in/company/{symbol}/consolidated/'
        html = archive['responses'][page]['body']
        company = BeautifulSoup(html,'html.parser').select_one('#company-info')['data-company-id']
        selected.append(page)
        details = [(url,response) for url,response in archive['responses'].items()
                   if f'/api/company/{company}/' in url and 'consolidated=' in url]
        selected.append(next(url for url,response in details if response['status']==200))
        selected.append(next(url for url,response in details if response['status']==403))
    gate = RequestGate(interval=2)
    results = []
    output = ROOT/'tmp/large-scale/live-clients.json'
    for engine,factory in [('httpx',Client),('curl_cffi',Session)]:
        kwargs = {'follow_redirects':False} if engine=='httpx' else {'allow_redirects':False}
        with factory(timeout=30,**kwargs) as session:
            for url in selected:
                check_url(url)
                gate.acquire()
                start = time.perf_counter()
                response = session.get(url)
                row = {'engine':engine,'url':url,'status':response.status_code,
                       'seconds':time.perf_counter()-start,
                       'expected_status':archive['responses'][url]['status']}
                if response.status_code==200 and urlsplit(url).path.startswith('/api/'):
                    current = json.dumps(response.json(),sort_keys=True)
                    previous = json.dumps(json.loads(archive['responses'][url]['body']),sort_keys=True)
                    row['json_equal'] = current==previous
                    row['sha256'] = hashlib.sha256(current.encode()).hexdigest()
                results.append(row)
                output.write_text(json.dumps(results,indent=2))
                print(json.dumps(row),flush=True)
                if response.status_code==429:
                    print('Rate limited. Ending all live comparisons.',flush=True)
                    return


if __name__=='__main__':
    main()
