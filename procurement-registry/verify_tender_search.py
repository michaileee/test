import sys
from pathlib import Path
sys.path.insert(0, str(Path('procurement_app').resolve()))
import tender_registry as t
class Response:
    def __init__(self, rows, total=12, page=1, last=3):
        self.text = '<table id="list_apps_by_subject">' + ''.join(f'<tr id="A{i}"></tr>' for i in rows) + f'</table><div class="pager">{total} ჩანაწერი (გვერდი: {page}/{last})</div>'
    def raise_for_status(self): pass
class Session:
    def __init__(self, broken=None): self.calls=[];self.broken=broken
    def post(self, url, data, **kwargs):
        self.calls.append(('POST',dict(data)))
        return Response(range(1,5))
    def get(self,url,params,**kwargs):
        self.calls.append(('GET',dict(params)))
        page=params['page']
        if self.broken=='repeated': return Response(range(1,5),page=page)
        if self.broken=='wrong_page': return Response(range(5,9),page=1)
        if self.broken=='changed_total': return Response(range(5,9),total=13,page=page)
        return Response(range((page-1)*4+1,page*4+1),page=page)
payload={'app_date_from':'01.01.2024','app_date_tlll':'31.12.2025'}
session=Session()
assert len(t.collect_search_rows(session,payload))==12
assert session.calls==[('POST',payload),('GET',{'action':'search_app','page':2}),('GET',{'action':'search_app','page':3})]
for broken in ('repeated','wrong_page','changed_total'):
    try: t.collect_search_rows(Session(broken),payload)
    except ValueError: pass
    else: raise AssertionError(broken)
print('Passed: one POST, all pages via GET, unchanged date range; repeated/wrong/changed pages rejected.')

class Empty(Session):
    def post(self,*args,**kwargs): return Response([],total=0,page=1,last=0)
assert t.collect_search_rows(Empty(),payload)==[]
