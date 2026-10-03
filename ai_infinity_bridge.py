#!/usr/bin/env python3
"""AI Infinity 200 local bridge.
Only allowlisted actions are executed. The server can never send shell code or
arbitrary programs to this bridge.

Device mode works with standard Python. On Android/Termux, optional
termux-api binaries enable notifications and URL opening.
Browser mode requires Playwright + Chromium on a trusted computer.
"""
from __future__ import annotations
import argparse, json, os, platform, shutil, subprocess, time, urllib.parse, urllib.request
from pathlib import Path


def api(base, path, payload):
    data=json.dumps(payload).encode()
    req=urllib.request.Request(base.rstrip('/')+path,data=data,headers={'content-type':'application/json','accept':'application/json'},method='POST')
    with urllib.request.urlopen(req,timeout=30) as r:
        return json.loads(r.read(512*1024).decode())


def public_url(url):
    p=urllib.parse.urlparse(url)
    if p.scheme not in ('http','https') or not p.hostname: return False
    h=p.hostname.lower().rstrip('.')
    if h in {'localhost','localhost.localdomain','metadata','metadata.google.internal','host.docker.internal'}: return False
    try:
        import ipaddress, socket
        infos=socket.getaddrinfo(h,p.port or (443 if p.scheme=='https' else 80),type=socket.SOCK_STREAM)
        for item in infos:
            ip=ipaddress.ip_address(item[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified: return False
    except Exception: return False
    return True


def browser_caps():
    try:
        import playwright  # noqa: F401
        return ['browser.web']
    except Exception:
        return []


def device_caps():
    caps=['device.ping','device.info']
    if shutil.which('termux-notification'): caps.append('device.notify')
    if shutil.which('termux-open-url'): caps.append('device.open_url')
    return caps


def browser_execute(page, action, cmd):
    timeout=int(cmd.get('timeout_ms') or 15000)
    if action=='browser.navigate':
        url=str(cmd.get('url') or '')
        if not public_url(url): return 'failed',{'error':'blocked non-public URL'}
        resp=page.goto(url,wait_until='domcontentloaded',timeout=timeout)
        status=getattr(resp,'status',lambda:None)() if resp else None
        return 'completed',{'observation':{'url':page.url,'title':page.title(),'http_status':status}}
    if action=='browser.click':
        sel=str(cmd.get('selector') or '')
        if not sel: return 'failed',{'error':'selector required'}
        page.locator(sel).click(timeout=timeout)
        return 'completed',{'observation':{'url':page.url,'title':page.title()}}
    if action=='browser.type':
        sel=str(cmd.get('selector') or ''); text=str(cmd.get('text') or '')
        if not sel: return 'failed',{'error':'selector required'}
        page.locator(sel).fill(text,timeout=timeout)
        return 'completed',{'observation':{'url':page.url,'title':page.title()}}
    if action=='browser.submit':
        sel=str(cmd.get('selector') or '')
        if sel: page.locator(sel).press('Enter',timeout=timeout)
        else: page.keyboard.press('Enter')
        return 'completed',{'observation':{'url':page.url,'title':page.title()}}
    if action=='browser.extract':
        txt=page.locator('body').inner_text(timeout=timeout)[:10000]
        return 'completed',{'observation':{'url':page.url,'title':page.title(),'text':txt}}
    return 'unsupported',{'error':'unsupported browser action'}


def device_execute(action, cmd):
    if action=='device.ping': return 'completed',{'device':'online','timestamp':time.time()}
    if action=='device.info': return 'completed',{'device_id':platform.node(),'system':platform.system(),'release':platform.release(),'machine':platform.machine(),'python':platform.python_version(),'bridge_version':'200'}
    if action=='device.notify':
        text=str(cmd.get('text') or '')[:2000]; exe=shutil.which('termux-notification')
        if not exe: return 'unsupported',{'error':'termux-notification not installed'}
        r=subprocess.run([exe,'--title','AI Infinity','--content',text],capture_output=True,text=True,timeout=15)
        return ('completed' if r.returncode==0 else 'failed'),{'stdout':r.stdout[-500:],'stderr':r.stderr[-500:]}
    if action=='device.open_url':
        url=str(cmd.get('url') or '')
        if not public_url(url): return 'failed',{'error':'blocked non-public URL'}
        exe=shutil.which('termux-open-url')
        if exe:
            r=subprocess.run([exe,url],capture_output=True,text=True,timeout=15)
            return ('completed' if r.returncode==0 else 'failed'),{'stdout':r.stdout[-500:],'stderr':r.stderr[-500:]}
        import webbrowser
        return ('completed' if webbrowser.open(url) else 'failed'),{'opened':url}
    return 'unsupported',{'error':'unsupported device action'}


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--server',required=True); ap.add_argument('--kind',choices=['browser','device'],required=True)
    ap.add_argument('--name',required=True); ap.add_argument('--code',required=True); ap.add_argument('--headful',action='store_true')
    args=ap.parse_args()
    store=Path('bridge_credential.json')
    token=None; runtime_id=None
    if store.exists():
        try:
            saved=json.loads(store.read_text()); token=saved.get('bridge_token'); runtime_id=saved.get('runtime_id')
        except Exception: pass
    caps=browser_caps() if args.kind=='browser' else device_caps()
    if args.kind=='browser' and 'browser.web' not in caps:
        raise SystemExit('Browser mode needs: pip install playwright && playwright install chromium')
    if not token:
        claim=api(args.server,'/activation/pair/claim',{'code':args.code,'kind':args.kind,'name':args.name,'capabilities':caps,'metadata':{'platform':platform.platform(),'bridge_version':'200'}})
        token=claim['bridge_token']; runtime_id=claim['runtime_id']
        store.write_text(json.dumps({'runtime_id':runtime_id,'bridge_token':token,'kind':args.kind,'name':args.name}))
        try: os.chmod(store,0o600)
        except Exception: pass
    page=None; browser=None; pw=None
    if args.kind=='browser':
        from playwright.sync_api import sync_playwright
        pw=sync_playwright().start(); browser=pw.chromium.launch(headless=not args.headful); page=browser.new_page()
    print(json.dumps({'status':'paired','runtime_id':runtime_id,'kind':args.kind,'name':args.name,'capabilities':caps},indent=2))
    last_hb=0
    try:
        while True:
            if time.time()-last_hb>=10:
                api(args.server,'/bridge/heartbeat',{'runtime_id':runtime_id,'bridge_token':token,'healthy':True,'capabilities':caps,'metadata':{'platform':platform.platform(),'bridge_version':'200'}}); last_hb=time.time()
            pol=api(args.server,'/bridge/poll',{'runtime_id':runtime_id,'bridge_token':token})
            if pol.get('status')=='command':
                c=pol['command']; action=c['action']; cmd=c.get('command') or {}
                try:
                    status,result=browser_execute(page,action,cmd) if action.startswith('browser.') else device_execute(action,cmd)
                except Exception as exc:
                    status,result='failed',{'error':str(exc)[:500]}
                api(args.server,'/bridge/result',{'runtime_id':runtime_id,'bridge_token':token,'command_id':c['id'],'status':status,'result':result,'error':result.get('error') if isinstance(result,dict) else None})
            time.sleep(2)
    finally:
        if browser: browser.close()
        if pw: pw.stop()

if __name__=='__main__': main()
