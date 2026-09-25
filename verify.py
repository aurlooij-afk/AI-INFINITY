#!/usr/bin/env python3
import json
import sys
import urllib.request

base=(sys.argv[1] if len(sys.argv)>1 else "http://127.0.0.1:18775").rstrip("/")
paths=[
    "/infinity/2300/health",
    "/infinity/2300/self-test",
    "/infinity/2300/status",
    "/infinity/2300/decoder",
    "/infinity/2300/organization",
    "/infinity/2300/economy",
    "/infinity/2300/free/ecosystem",
    "/infinity/final2260/self-test",
]
failed=[]
for path in paths:
    try:
        with urllib.request.urlopen(base+path,timeout=30) as r:
            data=json.load(r)
        passed=data.get("passed", True)
        print(f"PASS {path} :: status={data.get('status')} version={data.get('version')} passed={passed}")
        if passed is False:
            failed.append(path)
    except Exception as exc:
        failed.append(path)
        print(f"FAIL {path} :: {exc}")
try:
    with urllib.request.urlopen(base+"/infinity/2300/decoder",timeout=30) as r:
        d=json.load(r)
    expected={"layers":100,"words_per_description":[100]}
    if d.get("counts")!=expected:
        failed.append("/infinity/2300/decoder-contract")
        print("FAIL decoder-contract",d.get("counts"))
    else:
        print("PASS decoder-contract :: 100 layers x 100 words")
except Exception as exc:
    failed.append("/infinity/2300/decoder-contract")
    print("FAIL decoder-contract",exc)
print("ALL PASS" if not failed else "FAILED: "+", ".join(failed))
sys.exit(0 if not failed else 1)
