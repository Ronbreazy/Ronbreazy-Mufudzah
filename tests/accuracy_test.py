#!/usr/bin/env python3
"""
accuracy_test.py -- asks the RUNNING chatbot 25 IECMS questions and checks it
answers each from the right page of the IECMS Getting Started Guide, then
checks 5 off-topic questions are refused (Chapter 5, Test Case 2).
Start the backend first (python run.py) with the guide in knowledge_base/.
"""
import json, urllib.request, re
Q = [
 ("How do I register an account on IECMS?", {12,13,14}),
 ("How do I sign up for IECMS?", {12,13,14}),
 ("What is the IECMS website address?", {11}),
 ("How do I access the IECMS?", {11}),
 ("How do I log in to IECMS?", {15}),
 ("I forgot my password", {16,17,18}),
 ("How do I reset my password?", {16,17,18}),
 ("How do I change my password?", {20,21,16,17}),
 ("How do I edit my profile details?", {19,20,21}),
 ("How do I file a case?", {23,24,25,26,27}),
 ("How do I add a party to my case?", {24,25,28}),
 ("How do I attach documents to a case?", {26,27}),
 ("How do I add a legal entity as a party?", {35,36,37,38,39}),
 ("How do I sign a document?", {40,41,42,43,44,45,46,47}),
 ("How do I merge documents into one PDF?", {48,49,50}),
 ("How do I submit a case?", {51,28}),
 ("How do I pay stamp duty for my case?", {52,53,54,55}),
 ("How do I upload my payment receipt?", {52,53,54}),
 ("What is My Workspace?", {56,57}),
 ("How do I search for my cases?", {59,60,61}),
 ("How do I view my notifications?", {62,63}),
 ("How do I see court dates on the calendar?", {64,65,66}),
 ("How do I export calendar events?", {66,67}),
 ("How do I log out of IECMS?", {71}),
 ("What does IECMS stand for?", {7,8,10}),
]
OUT = ["What is the capital of France?", "Who will win the football league this year?",
       "Can you recommend a good restaurant in Harare?", "How do I cook sadza?", "What is the weather tomorrow?"]
def ask(m):
    r=urllib.request.Request("http://localhost:8000/chat",data=json.dumps({"message":m}).encode(),method="POST",headers={"Content-Type":"application/json"})
    return json.loads(urllib.request.urlopen(r,timeout=30).read())
ok=0; rows=[]
for q,pages in Q:
    d=ask(q); src=d["sources"][0] if d["sources"] else ""
    m=re.search(r"page (\d+)",src); pg=int(m.group(1)) if m else None
    good = d["grounded"] and pg in pages
    ok+=good; rows.append((q, sorted(pages), pg, "correct" if good else "WRONG"))
for r in rows: print(r)
ref=sum(1 for q in OUT if not ask(q)["grounded"])
print(f"IECMS questions answered from the right page: {ok}/{len(Q)}")
print(f"Off-topic questions correctly refused: {ref}/{len(OUT)}")
