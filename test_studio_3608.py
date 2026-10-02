from fastapi.testclient import TestClient
import main
c=TestClient(main.app)
def test_health():
 r=c.get('/infinity/3608/health'); assert r.status_code==200; assert r.json()['adaptive_learning'] is True
def test_studio():
 r=c.post('/infinity/3608/studio',json={'topic':'space exploration','objective':'make an intelligent cinematic explainer','format':'short','duration':45}); assert r.status_code==200; j=r.json(); assert j['status']=='blueprint_ready'; assert j['job_id']
def test_skills():
 r=c.get('/infinity/3608/skills'); assert r.status_code==200; assert r.json()['self_upgradeable'] is True
