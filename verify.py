import ast, os, sys, tempfile, sqlite3, zipfile
ROOT=os.path.dirname(os.path.abspath(__file__))
os.environ['AI_INFINITY_DATA_DIR']=os.path.join(tempfile.gettempdir(),'ai-infinity-2700-verify')
os.makedirs(os.environ['AI_INFINITY_DATA_DIR'],exist_ok=True)
sys.path.insert(0,ROOT)
import main
checks=[]
def ck(name, ok, err=''):
    checks.append((name,bool(ok),err))

ck('compile', True)
r=main._2700_self_test(); ck('2700 self-test',r['passed'])
r2600=main._f2600_self_test(); ck('2600 preservation self-test',r2600['passed'])
ck('100 closure steps',len(main.FINAL100_2700)==100)
ck('10x10 closure',len(main._FINAL2700_GROUPS)==10 and all(len(x[1])==10 for x in main._FINAL2700_GROUPS))
ck('health contract',main._2700_health()['version']=='TARGET-2050.2700')
ck('plan safe command',main._2700_command_plan({'command':'open https://example.com'})['executable_now'] is True)
sp=main._2700_command_plan({'command':'send Slack message: hello'})
ck('side effect requires approval',sp['approval_required'] is True and sp['executable_now'] is False)
ck('route count',all(any(getattr(x,'path',None)==p for x in main.app.routes) for p in ['/infinity/2600/ui','/infinity/2600/self-test','/infinity/2700/ui','/infinity/2700/self-test','/infinity/2700/command/execute']))
ck('secret redaction',main._2700_redact({'token':'x'})['token']=='[REDACTED]')
ck('safety invariants',main._2700_status()['safety']['arbitrary_code_execution'] is False and main._2700_status()['safety']['automatic_uncertain_replay'] is False)
source=open(os.path.join(ROOT,'main.py'),encoding='utf-8').read()
tree=ast.parse(source)
imports={n.names[0].name.split('.')[0] for n in ast.walk(tree) if isinstance(n,ast.Import) for _ in [0]}
imports |= {n.module.split('.')[0] for n in ast.walk(tree) if isinstance(n,ast.ImportFrom) and n.module}
ck('stdlib/no new runtime dependency',not ({'requests','httpx'} & imports))
for n,ok,e in checks:
    print(('PASS' if ok else 'FAIL')+': '+n+((' — '+e) if e else ''))
print('TOTAL',len(checks),'FAILED',sum(not x[1] for x in checks))
raise SystemExit(1 if any(not x[1] for x in checks) else 0)
