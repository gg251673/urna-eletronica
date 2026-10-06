import io
import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor
import pytest
from PIL import Image
from app import create_app

@pytest.fixture
def setup(tmp_path):
    app=create_app({'TESTING':True,'DATA_DIR':str(tmp_path)})
    client=app.test_client()
    token=client.get('/api/admin/session').json['csrf']
    r=client.post('/api/admin/login',json={'username':'admin','password':'admin'},headers={'X-CSRF-Token':token})
    return app,client,{'X-CSRF-Token':r.json['csrf']},tmp_path

def image():
    b=io.BytesIO();Image.new('RGB',(40,40),'green').save(b,'PNG');b.seek(0);return b

def prepare(client,h):
    assert client.post('/api/admin/candidates',data={'name':'Ana','number':'01','photo':(image(),'ana.png')},headers=h).status_code==200
    cid=client.get('/api/admin/candidates').json[0]['id']
    rid=client.post('/api/admin/rounds',json={'title':'Turma','office':'Representante','digits':2,'candidates':[cid]},headers=h).json['id']
    assert client.post(f'/api/admin/rounds/{rid}/open',headers=h).status_code==200
    return cid,rid

def send(client,rid,kind='number',number='01',key=None):
    return client.post('/api/votes',json={'round_id':rid,'kind':kind,'number':number},headers={'Idempotency-Key':key or str(uuid.uuid4())})

def test_authentication(setup):
    app,c,h,_=setup; anon=app.test_client(); t=anon.get('/api/admin/session').json['csrf']
    assert anon.post('/api/admin/login',json={'username':'admin','password':'wrong'},headers={'X-CSRF-Token':t}).status_code==401
    for url in ['/candidates','/rounds','/results/1','/results/1/csv/history']:
        assert anon.get('/api/admin'+url).status_code==401
    assert c.post('/api/admin/rounds',json={}).status_code==403
    assert c.post('/api/admin/logout',headers=h).status_code==200
    assert c.get('/api/admin/candidates').status_code==401

def test_complete_lifecycle(setup):
    app,c,h,path=setup;cid,rid=prepare(c,h)
    assert send(c,rid,number='0').status_code==400
    key=str(uuid.uuid4()); a=send(c,rid,key=key);b=send(c,rid,key=key)
    assert a.status_code==201 and a.json['id']==b.json['id']
    assert send(c,rid,number='99',key=key).status_code==409
    assert send(c,rid,'blank').status_code==201
    assert send(c,rid,number='99').status_code==201
    report=c.get(f'/api/admin/results/{rid}').json
    assert (report['total'],report['valid'],report['blank'],report['null'])==(3,1,1,1)
    assert report['ranking'][0]['percent']==100
    assert c.put(f'/api/admin/candidates/{cid}',data={'name':'Alterado','number':'02'},headers=h).status_code==200
    assert c.get('/api/round').json['candidates'][0]['name']=='Ana'
    assert c.delete(f'/api/admin/candidates/{cid}',headers=h).status_code==200
    assert c.post(f'/api/admin/rounds/{rid}/close',headers=h).status_code==200
    assert send(c,rid).status_code==409
    assert send(c,rid,key=key).json['id']==a.json['id']
    assert c.get(f'/api/admin/results/{rid}').json['total']==3
    assert c.get(f'/api/admin/results/{rid}/csv/history').status_code==200
    assert c.get(f'/api/admin/results/{rid}/csv/ranking').status_code==200
    assert c.get('/api/round').json['round'] is None
    # Reabrir a aplicação preserva banco, autenticação e fotos.
    restored=create_app({'TESTING':True,'DATA_DIR':str(path)})
    with sqlite3.connect(path/'urna.db') as conn: assert conn.execute('SELECT COUNT(*) FROM votes').fetchone()[0]==3
    assert list((path/'photos').glob('*.jpg'))

def test_candidate_validation(setup):
    _,c,h,_=setup
    assert c.post('/api/admin/candidates',data={'name':'Ana','number':'01','photo':(io.BytesIO(b'invalid'),'x.png')},headers=h).status_code==400
    prepare(c,h)
    assert c.post('/api/admin/candidates',data={'name':'Outra','number':'01','photo':(image(),'x.png')},headers=h).status_code==409
    assert c.post('/api/admin/rounds',json={'title':'T','office':'O','digits':3,'candidates':[1]},headers=h).status_code==400
    assert c.post('/api/admin/rounds',json={'title':'T','office':'O','digits':2,'candidates':[]},headers=h).status_code==400

def test_one_open_and_independent_results(setup):
    _,c,h,_=setup;cid,rid=prepare(c,h);send(c,rid)
    second=c.post('/api/admin/rounds',json={'title':'Segundo','office':'Cargo','digits':2,'candidates':[cid]},headers=h).json['id']
    assert c.post(f'/api/admin/rounds/{second}/open',headers=h).status_code==409
    c.post(f'/api/admin/rounds/{rid}/close',headers=h)
    assert c.post(f'/api/admin/rounds/{second}/open',headers=h).status_code==200
    assert c.get(f'/api/admin/results/{second}').json['total']==0
    assert c.get(f'/api/admin/results/{rid}').json['total']==1

def test_concurrent_duplicate_and_close(setup):
    app,c,h,path=setup;_,rid=prepare(c,h);key=str(uuid.uuid4())
    with ThreadPoolExecutor(max_workers=8) as pool:
        responses=list(pool.map(lambda _:send(app.test_client(),rid,key=key),range(8)))
    assert sum(r.status_code==201 for r in responses)==1
    assert len({r.json['id'] for r in responses})==1
    # Encerramento e inserções competem pela mesma transação de escrita.
    with ThreadPoolExecutor(max_workers=8) as pool:
        close=pool.submit(lambda:c.post(f'/api/admin/rounds/{rid}/close',headers=h))
        votes=[pool.submit(lambda:send(app.test_client(),rid)) for _ in range(7)]
        assert close.result().status_code==200
        assert all(v.result().status_code in (201,409) for v in votes)
    assert send(c,rid).status_code==409
    with sqlite3.connect(path/'urna.db') as conn:
        closed=conn.execute('SELECT closed_at FROM rounds WHERE id=?',(rid,)).fetchone()[0]
        assert conn.execute('SELECT COUNT(*) FROM votes WHERE created_at>?',(closed,)).fetchone()[0]==0

def test_password(setup):
    app,c,h,_=setup
    assert c.post('/api/admin/password',json={'current':'bad','password':'longpassword'},headers=h).status_code==400
    assert c.post('/api/admin/password',json={'current':'admin','password':'newpassword'},headers=h).status_code==200
    t=c.get('/api/admin/session').json['csrf']
    assert c.post('/api/admin/login',json={'username':'admin','password':'admin'},headers={'X-CSRF-Token':t}).status_code==401
    assert c.post('/api/admin/login',json={'username':'admin','password':'newpassword'},headers={'X-CSRF-Token':t}).status_code==200
