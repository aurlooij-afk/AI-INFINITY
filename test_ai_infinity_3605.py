from pathlib import Path
from fastapi.testclient import TestClient
import main

client = TestClient(main.app)

def test_3605_health():
    r = client.get('/infinity/3605/health')
    assert r.status_code == 200
    j = r.json()
    assert j['website_first'] is True
    assert j['local_media_factory'] is True
    assert j['local_voice'] is True
    assert j['local_music'] is True
    assert j['local_mp4'] is True
    assert j['one_command_launch'] is True

def test_public_website_uses_reference_workspace():
    html = client.get('/').text
    for marker in ('Complete production flow', 'ONE COMMAND', 'GENIUS BRAIN', 'FINAL OUTPUT', 'ORGANIZATION', 'Start AI Infinity'):
        assert marker in html
    assert 'reality boundary' not in html.lower()

def test_media_factory_is_real_downloadable_mp4():
    r = client.post('/infinity/3605/media/factory', json={
        'title': 'AI Infinity Smoke Test',
        'objective': 'Create a short introduction to AI Infinity',
        'audience': 'new users',
        'format': 'video',
    })
    assert r.status_code == 200
    j = r.json()
    assert j['automatic_editing'] is True
    assert j['download_url'].endswith('.mp4')
    mp = client.get(j['download_url'])
    assert mp.status_code == 200
    assert mp.headers.get('content-type', '').startswith('video/mp4')
    assert len(mp.content) > 100_000
