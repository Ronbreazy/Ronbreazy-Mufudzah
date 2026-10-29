#!/usr/bin/env python3
"""
load_test.py -- sends requests from 1, 10 and 50 simultaneous users to the
RUNNING backend and reports failures and response times (Chapter 5,
Test Case 3). Start the backend first (python run.py).
Note: it runs the Jargon Simplifier, so it adds entries to the Case Law
library -- run it on a test copy, not live data.
"""
import json, time, threading, urllib.request, statistics
B="http://localhost:8000"
REQ=[("POST","/chat",{"message":"How do I file a case?"}),("GET","/directory?q=harare",None),
     ("GET","/dictionary?q=affidavit",None),("GET","/case-law",None),("GET","/forum/posts",None),
     ("POST","/simplify",{"text":"The applicant filed an affidavit. Accordingly, it is ordered that the respondent is interdicted."})]
def one(m,p,b):
    r=urllib.request.Request(B+p,data=json.dumps(b).encode() if b else None,method=m,headers={"Content-Type":"application/json"})
    t=time.perf_counter()
    try: urllib.request.urlopen(r,timeout=30).read(); ok=True
    except Exception: ok=False
    return time.perf_counter()-t, ok
def run(users, rounds=10):
    res=[]; lock=threading.Lock()
    def user(i):
        for k in range(rounds):
            d,ok=one(*REQ[(i+k)%len(REQ)])
            with lock: res.append((d,ok))
    ts=[threading.Thread(target=user,args=(i,)) for i in range(users)]
    t0=time.perf_counter(); [t.start() for t in ts]; [t.join() for t in ts]; wall=time.perf_counter()-t0
    ds=sorted(d for d,_ in res); fails=sum(1 for _,ok in res if not ok)
    p95=ds[int(0.95*len(ds))-1]
    print(f"{users} users | {len(res)} requests | failures {fails} | median {statistics.median(ds)*1000:.0f} ms | 95th pct {p95*1000:.0f} ms | max {ds[-1]*1000:.0f} ms | {len(res)/wall:.0f} req/s")
for u in (1,10,50): run(u)
