from fastapi.testclient import TestClient
import main

client=TestClient(main.app)

def test_health():
    r=client.get('/infinity/3607/health')
    assert r.status_code==200
    j=r.json()
    assert j['long_form'] and j['short_form']
    assert j['fake_slideshow_fallback'] is False

def test_no_fake_output_without_motion_sources():
    r=client.post('/infinity/3607/content',json={'title':'Universe','objective':'Explain the universe clearly','format':'short','duration':30})
    assert r.status_code==200
    j=r.json()
    assert j['status'] in {'needs_visual_source','completed'}
    if j['status']=='needs_visual_source':
        assert 'No fake slideshow' in j['message']
