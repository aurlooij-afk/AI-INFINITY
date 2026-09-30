import os
import tempfile
os.environ['AI_INFINITY_DATA_DIR']=tempfile.mkdtemp(prefix='ai3603-test-')

import main
from fastapi.testclient import TestClient

client=TestClient(main.app)

def test_canonical_health():
    r=client.get('/health'); assert r.status_code==200; assert r.json()['version']=='TARGET-2050.3603'

def test_capabilities():
    r=client.get('/infinity/3601/genius/capabilities'); assert r.status_code==200

def test_source_health_and_fixed_price_rule():
    r=client.get('/infinity/3603/sources'); assert r.status_code==200
    assert r.json()['fixed_price_source']=='openbounty'
    assert not main._gp_fixed_price_evidence({'title':'Developer','description':'$40/hour','amount':40,'metadata_json':'{}'})['qualifies']
    assert main._gp_fixed_price_evidence({'title':'Test','description':'Fixed price','amount':20,'metadata_json':'{"fixed_price":true,"reward":20}'})['qualifies']

def test_public_activation_redacts_identity():
    data=client.get('/activation/status').json(); assert 'email' not in data.get('owner',{})
    assert client.get('/infinity/3603/activation/private').status_code==401

def test_bridge_capability_catalog():
    data=client.get('/infinity/3603/bridge-capabilities').json(); assert 'browser.submit' in data['catalog']['browser']

def test_legacy_self_tests():
    assert client.get('/infinity/3601/self-test').json()['passed']
    assert client.get('/infinity/3602/self-test').json()['passed']
    assert client.get('/infinity/3601/genius/self-test').json()['passed']

def test_final_regression():
    assert client.get('/infinity/3603/regression').json()['passed']
