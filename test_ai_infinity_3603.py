from fastapi.testclient import TestClient
import main

client = TestClient(main.app)

def test_health_and_version():
    r=client.get("/health")
    assert r.status_code==200
    assert r.json()["version"]=="TARGET-2050.3604"

def test_operating_workspace_ui():
    assert client.get("/genius").status_code==200
    assert client.get("/infinity/3603/ui").status_code==200

def test_regression_endpoint():
    j=client.get("/infinity/3603/regression").json()
    assert j["passed"] is True

def test_truthful_readiness():
    j=client.get("/infinity/3603/readiness").json()
    assert j["truthful"] is True
    assert j["transferable_now_requires_successful_payout_rail"] is True

def test_fixed_price_rule():
    assert main._gp_fixed_price_evidence({"title":"Task","description":"fixed price project","amount":25,"metadata_json":"{\"fixed_price\":true,\"reward\":25}"})["qualifies"]
    assert not main._gp_fixed_price_evidence({"title":"Job","description":"$50 per hour","amount":50,"metadata_json":"{}"})["qualifies"]

def test_command_research_is_real_engine(monkeypatch):
    monkeypatch.setattr(main, "_2600_public_research_direct", lambda objective, mission_id, limit: {"status":"completed","evidence":[],"query":objective})
    r=client.post("/infinity/3603/command",json={"command":"Research test topic"})
    assert r.status_code==200
    assert r.json()["stage"]=="research"

def test_command_create_is_persistent_engine(monkeypatch):
    monkeypatch.setattr(main, "_f2300_media_production_plan", lambda payload: {"media_id":"test-media","status":"created","objective":payload["objective"]})
    r=client.post("/infinity/3603/command",json={"command":"Create a video package"})
    assert r.status_code==200
    assert r.json()["stage"]=="create"


def test_consequential_approval_requires_operator_token():
    assert client.post('/approve/not-real').status_code == 401
    assert client.post('/reject/not-real').status_code == 401
    assert client.post('/execute/not-real', json={'approved': True}).status_code == 401

def test_approval_queue_requires_operator_token():
    assert client.get('/infinity/3603/approvals').status_code == 401

def test_readiness_does_not_claim_unhealthy_discovery_is_ready():
    j=client.get('/infinity/3603/readiness').json()
    assert j['discovery_ready'] == (j['healthy_source_count'] > 0)

def test_fixed_price_preparation_excludes_salary_only_rows():
    import json, time
    t=time.time()
    with main._db_lock, main.db() as db:
        db.execute('DELETE FROM ai3601_opportunities')
        for oid,title,meta,amt in [
            ('test-salary','Hourly task','{}',100),
            ('test-fixed','Fixed task',json.dumps({'fixed_price':True,'reward':250}),250),
        ]:
            db.execute("INSERT INTO ai3601_opportunities(opportunity_id,source,external_id,title,url,description,opportunity_type,currency,amount,amount_max,status,score,eligibility,metadata_json,discovered_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(oid,'test',oid,title,'https://example.com','public job','public_job','USD',amt,None,'discovered',1,'candidate',meta,t,t))
    prepared=main._3601_prepare(10,fixed_only=True)
    assert [x['opportunity_id'] for x in prepared] == ['test-fixed']

def test_bridge_capabilities_match_bridge_implementation():
    catalog=client.get('/infinity/3603/bridge-capabilities').json()['catalog']['browser']
    bridge=__import__('pathlib').Path('ai_infinity_bridge.py').read_text()
    for action in ['browser.navigate','browser.click','browser.type','browser.submit','browser.extract']:
        assert action in catalog and action in bridge


def test_workspace_session_isolated_and_manifest_complete():
    a=client.get('/infinity/3603/session'); b=client.get('/infinity/3603/session')
    assert a.status_code==200 and b.status_code==200
    assert a.json()['session_isolated'] is True
    m=client.get('/infinity/3603/ui-manifest').json()
    for x in ['chat','research','plan','discover_fixed_price','apply','create','mission','organization','economy','wallet','memory','activity','connections','authority','payout','persistence_snapshot']:
        assert x in m['actions']

def test_dashboard_and_core_workspace_routes():
    assert client.get('/infinity/3603/dashboard').status_code==200
    assert client.get('/infinity/3603/work').status_code==200
    assert client.get('/infinity/3603/activity').status_code==200

def test_ui_contains_real_action_controls():
    html=client.get('/infinity/3603/ui').text
    for token in ['Run command','Fixed-price work','Create plan','Discover now','Apply','Authority','Snapshot state','Voice']:
        assert token in html


def test_no_duplicate_path_method_routes_remain():
    seen=set()
    for route in main.app.routes:
        key=(getattr(route,'path',None),tuple(sorted(getattr(route,'methods',set()) or set())))
        assert key not in seen, key
        seen.add(key)

def test_reality_gateway_is_truthful_and_exposes_all_external_gates():
    j=client.get('/infinity/3604/reality')
    assert j.status_code==200
    assert j.json()['truthful'] is True
    for key in ['browser_external_actions','authorized_application_api','payment_verification','payout_rail','durable_state','periodic_wakeup']:
        assert key in j.json()['gates']

def test_cron_wakeup_requires_secret():
    assert client.post('/infinity/3604/cron').status_code==401

def test_github_persistence_endpoint_requires_operator():
    assert client.post('/infinity/3604/persistence/github').status_code==401

def test_ui_reality_tab_is_connected():
    html=client.get('/infinity/3603/ui').text
    for token in ['Reality','refreshReality','/infinity/3604/reality','wakeNow']:
        assert token in html
