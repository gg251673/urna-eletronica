import csv
import io
import json
import os
import secrets
import sqlite3
import uuid
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from flask import Flask, request, session, jsonify, render_template, send_from_directory, Response
from PIL import Image, UnidentifiedImageError
from werkzeug.security import generate_password_hash, check_password_hash

BASE = Path(__file__).resolve().parent

def create_app(test_config=None):
    app = Flask(__name__)
    app.config.update(DATA_DIR=os.environ.get('DATA_DIR', str(BASE / 'instance')), MAX_CONTENT_LENGTH=5 * 1024 * 1024, SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Strict', SESSION_COOKIE_SECURE=os.environ.get('COOKIE_SECURE') == '1')
    if test_config: app.config.update(test_config)
    data = Path(app.config['DATA_DIR']); data.mkdir(parents=True, exist_ok=True)
    (data / 'photos').mkdir(exist_ok=True)
    key = data / 'session.key'
    if not key.exists():
        with key.open('x') as f: f.write(secrets.token_hex(32))
        key.chmod(0o600)
    app.secret_key = key.read_text()
    def db():
        conn = sqlite3.connect(data / 'urna.db', timeout=20)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        return conn
    with db() as c:
        c.execute('PRAGMA journal_mode=WAL')
        c.executescript((BASE / 'migrations/001_initial.sql').read_text())
        c.execute('INSERT OR IGNORE INTO admins(username,password) VALUES (?,?)', ('admin', generate_password_hash('admin')))
    def now(): return datetime.now(timezone.utc).isoformat()
    def fail(message, status=400): return jsonify(error=message), status
    def protected(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not session.get('admin'): return fail('Faça login para continuar.', 401)
            return fn(*args, **kwargs)
        return wrapper
    @app.after_request
    def security_headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob:; object-src 'none'; frame-ancestors 'none'; base-uri 'self'"
        if request.path.startswith('/api/') or request.path == '/admin': response.headers['Cache-Control'] = 'no-store'
        return response
    @app.before_request
    def csrf():
        if request.is_json and not isinstance(request.get_json(silent=True), dict):
            return fail('Envie um objeto JSON válido.')
        if request.path.startswith('/api/admin') and request.method not in ('GET', 'HEAD', 'OPTIONS'):
            if not session.get('csrf') or not secrets.compare_digest(request.headers.get('X-CSRF-Token', ''), session['csrf']):
                return fail('Sessão de segurança inválida. Recarregue a página.', 403)
    @app.errorhandler(413)
    def large(_): return fail('Imagem excede 5 MB.', 413)
    @app.errorhandler(sqlite3.IntegrityError)
    def integrity(_): return fail('Número duplicado ou operação incompatível com o estado atual.', 409)
    @app.get('/')
    def home(): return render_template('vote.html')
    @app.get('/admin')
    def admin(): return render_template('admin.html')
    @app.get('/photos/<name>')
    def photo(name): return send_from_directory(data / 'photos', name)
    @app.get('/api/admin/session')
    def state():
        session.setdefault('csrf', secrets.token_hex(32))
        return jsonify(authenticated=bool(session.get('admin')), csrf=session['csrf'])
    @app.post('/api/admin/login')
    def login():
        body = request.get_json(silent=True) or {}
        if not isinstance(body.get('username', ''), str) or not isinstance(body.get('password', ''), str): return fail('Credenciais inválidas.')
        with db() as c: row = c.execute('SELECT * FROM admins WHERE username=?', (body.get('username', ''),)).fetchone()
        if not row or not check_password_hash(row['password'], body.get('password', '')): return fail('Usuário ou senha incorretos.', 401)
        session.clear(); session.update(admin=row['id'], csrf=secrets.token_hex(32))
        return jsonify(csrf=session['csrf'])
    @app.post('/api/admin/logout')
    @protected
    def logout(): session.clear(); return jsonify(ok=True)
    @app.post('/api/admin/password')
    @protected
    def password():
        b = request.get_json() or {}
        if not isinstance(b.get('current',''),str) or not isinstance(b.get('password',''),str): return fail('Senha inválida.')
        with db() as c:
            row = c.execute('SELECT password FROM admins WHERE id=?', (session['admin'],)).fetchone()
            if not check_password_hash(row['password'], b.get('current', '')): return fail('Senha atual incorreta.')
            if len(b.get('password', '')) < 8: return fail('Use uma senha com pelo menos 8 caracteres.')
            c.execute('UPDATE admins SET password=? WHERE id=?', (generate_password_hash(b['password']), session['admin']))
        session.clear(); return jsonify(ok=True)
    @app.get('/api/admin/candidates')
    @protected
    def candidates():
        with db() as c: return jsonify([dict(r) for r in c.execute('SELECT * FROM candidates ORDER BY name')])
    @app.route('/api/admin/candidates', methods=['POST'])
    @app.route('/api/admin/candidates/<int:cid>', methods=['PUT','DELETE'])
    @protected
    def candidate(cid=None):
        with db() as c:
            old = c.execute('SELECT * FROM candidates WHERE id=?', (cid,)).fetchone() if cid else None
            if cid and not old: return fail('Candidato não encontrado.',404)
            if request.method == 'DELETE':
                c.execute('DELETE FROM candidates WHERE id=?',(cid,)); return jsonify(ok=True)
            name=request.form.get('name','').strip(); number=request.form.get('number','').strip()
            if not name or len(name)>120 or not number.isascii() or not number.isdigit() or not 1<=len(number)<=10: return fail('Informe nome e número de 1 a 10 dígitos.')
            photo_name = old['photo'] if old else None
            file = request.files.get('photo')
            if file and file.filename:
                try:
                    im=Image.open(file.stream)
                    if im.format not in ('JPEG','PNG','WEBP') or im.width * im.height > 20_000_000: return fail('Use JPEG, PNG ou WebP com até 20 megapixels.')
                    im.load(); im=im.convert('RGB'); im.thumbnail((800,800))
                    photo_name=uuid.uuid4().hex+'.jpg'; im.save(data/'photos'/photo_name, quality=90)
                except (UnidentifiedImageError, OSError, Image.DecompressionBombError): return fail('Imagem inválida.')
            if not photo_name: return fail('Envie uma foto.')
            if cid: c.execute('UPDATE candidates SET name=?,number=?,photo=? WHERE id=?',(name,number,photo_name,cid))
            else: c.execute('INSERT INTO candidates(name,number,photo) VALUES (?,?,?)',(name,number,photo_name))
        return jsonify(ok=True)
    @app.get('/api/admin/rounds')
    @protected
    def rounds():
        with db() as c: return jsonify([dict(r) for r in c.execute('SELECT * FROM rounds ORDER BY id DESC')])
    @app.post('/api/admin/rounds')
    @protected
    def new_round():
        b=request.get_json() or {}
        title=str(b.get('title','')).strip(); office=str(b.get('office','')).strip(); digits=b.get('digits'); ids=b.get('candidates',[])
        if not title or not office or len(title)>120 or len(office)>120 or type(digits)!=int or not 1<=digits<=10 or not isinstance(ids,list) or not ids or any(type(i)!=int for i in ids): return fail('Preencha título, cargo, dígitos e participantes.')
        with db() as c:
            rows=c.execute('SELECT * FROM candidates WHERE id IN ('+','.join('?' for _ in ids)+')',ids).fetchall()
            if len(rows)!=len(set(ids)) or any(len(r['number'])!=digits for r in rows): return fail('Todos os candidatos precisam ter a quantidade de dígitos configurada.')
            rid=c.execute('INSERT INTO rounds(title,office,digits) VALUES (?,?,?)',(title,office,digits)).lastrowid
            for r in rows: c.execute('INSERT INTO round_candidates(round_id,source_id,name,number,photo) VALUES (?,?,?,?,?)',(rid,r['id'],r['name'],r['number'],r['photo']))
        return jsonify(id=rid)
    @app.post('/api/admin/rounds/<int:rid>/<action>')
    @protected
    def change_round(rid,action):
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            r=c.execute('SELECT * FROM rounds WHERE id=?',(rid,)).fetchone()
            if not r: return fail('Turno não encontrado.',404)
            if action=='open' and r['status']=='draft':
                if c.execute("SELECT 1 FROM rounds WHERE status='open'").fetchone(): return fail('Já existe um turno aberto.',409)
                rows=c.execute('SELECT * FROM round_candidates WHERE round_id=?',(rid,)).fetchall()
                # Atualiza a seleção ao abrir; depois permanece congelada.
                fresh=[]
                for rc in rows:
                    candidate=c.execute('SELECT * FROM candidates WHERE id=?',(rc['source_id'],)).fetchone()
                    if not candidate or len(candidate['number'])!=r['digits']: return fail('Participante removido ou com número incompatível.')
                    fresh.append(candidate)
                if not fresh: return fail('Selecione pelo menos um candidato.')
                c.execute('DELETE FROM round_candidates WHERE round_id=?',(rid,))
                for candidate in fresh: c.execute('INSERT INTO round_candidates(round_id,source_id,name,number,photo) VALUES (?,?,?,?,?)',(rid,candidate['id'],candidate['name'],candidate['number'],candidate['photo']))
                c.execute("UPDATE rounds SET status='open',opened_at=? WHERE id=?",(now(),rid))
            elif action=='close' and r['status']=='open': c.execute("UPDATE rounds SET status='closed',closed_at=? WHERE id=?",(now(),rid))
            else: return fail('Transição de turno inválida.',409)
        return jsonify(ok=True)
    @app.get('/api/round')
    def active():
        with db() as c:
            r=c.execute("SELECT * FROM rounds WHERE status='open'").fetchone()
            if not r: return jsonify(round=None)
            return jsonify(round=dict(r),candidates=[dict(x) for x in c.execute('SELECT id,name,number,photo FROM round_candidates WHERE round_id=?',(r['id'],))])
    @app.post('/api/votes')
    def vote():
        b=request.get_json(silent=True) or {}; key=request.headers.get('Idempotency-Key','')
        if not 16<=len(key)<=100 or type(b.get('round_id'))!=int or b.get('kind') not in ('number','blank'): return fail('Envio inválido.')
        number=b.get('number','') if b['kind']=='number' else ''
        if not isinstance(number,str): return fail('Número inválido.')
        payload=json.dumps([b['round_id'],b['kind'],number])
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            existing=c.execute('SELECT * FROM votes WHERE idempotency=?',(key,)).fetchone()
            if existing:
                if existing['payload']!=payload: return fail('Chave já utilizada em outra escolha.',409)
                return jsonify(id=existing['id'],registered=True)
            r=c.execute('SELECT * FROM rounds WHERE id=?',(b['round_id'],)).fetchone()
            if not r or r['status']!='open': return fail('Não há turno aberto para este voto.',409)
            if b['kind']=='number' and (not number.isascii() or not number.isdigit() or len(number)!=r['digits']): return fail('Complete o número antes de confirmar.')
            candidate=c.execute('SELECT id FROM round_candidates WHERE round_id=? AND number=?',(r['id'],number)).fetchone() if b['kind']=='number' else None
            kind='blank' if b['kind']=='blank' else ('valid' if candidate else 'null'); vid=str(uuid.uuid4())
            c.execute('INSERT INTO votes VALUES (?,?,?,?,?,?,?)',(vid,r['id'],now(),kind,candidate['id'] if candidate else None,key,payload))
        return jsonify(id=vid,registered=True),201
    def report(rid):
        with db() as c:
            r=c.execute('SELECT * FROM rounds WHERE id=?',(rid,)).fetchone()
            if not r: return None
            history=[dict(x) for x in c.execute('SELECT v.id,v.created_at,v.kind,rc.name,rc.number FROM votes v LEFT JOIN round_candidates rc ON rc.id=v.candidate_id WHERE v.round_id=? ORDER BY v.created_at DESC',(rid,))]
            ranking=[dict(x) for x in c.execute("SELECT rc.id,rc.name,rc.number,rc.photo,COUNT(v.id) AS votes FROM round_candidates rc LEFT JOIN votes v ON rc.id=v.candidate_id WHERE rc.round_id=? GROUP BY rc.id ORDER BY votes DESC,rc.name",(rid,))]
            totals={k:sum(x['kind']==k for x in history) for k in ('valid','blank','null')}
            for x in ranking:
                x['percent']=round(100*x['votes']/totals['valid'],2) if totals['valid'] else 0
                x['tie']=sum(y['votes']==x['votes'] for y in ranking)>1
            return dict(round=dict(r),history=history,ranking=ranking,total=len(history),**totals)
    @app.get('/api/admin/results/<int:rid>')
    @protected
    def results(rid):
        result=report(rid)
        return jsonify(result) if result else fail('Turno não encontrado.',404)
    @app.get('/api/admin/results/<int:rid>/csv/<mode>')
    @protected
    def export(rid,mode):
        result=report(rid)
        if not result or mode not in ('history','ranking'): return fail('Exportação não encontrada.',404)
        from zoneinfo import ZoneInfo
        out=io.StringIO(); writer=csv.writer(out,delimiter=';')
        def safe(v):
            s=str(v if v is not None else '')
            return "'"+s if s.startswith(('=','+','-','@','\t','\r')) else s
        if mode=='history':
            writer.writerow(['ID','Horário America/Sao_Paulo','Tipo','Nome','Número'])
            for x in result['history']: writer.writerow([safe(x['id']),datetime.fromisoformat(x['created_at']).astimezone(ZoneInfo('America/Sao_Paulo')).isoformat(),x['kind'],safe(x['name']),safe(x['number'])])
        else:
            writer.writerow(['Turno','Cargo','Status','Abertura UTC','Encerramento UTC','Total','Válidos','Brancos','Nulos'])
            r=result['round']; writer.writerow([safe(r['title']),safe(r['office']),r['status'],r['opened_at'],r['closed_at'],result['total'],result['valid'],result['blank'],result['null']])
            writer.writerow(['Nome','Número','Votos','Percentual dos válidos','Empate'])
            for x in result['ranking']: writer.writerow([safe(x['name']),safe(x['number']),x['votes'],x['percent'],'Sim' if x['tie'] else 'Não'])
        return Response('\ufeff'+out.getvalue(),mimetype='text/csv',headers={'Content-Disposition':f'attachment; filename="turno-{rid}-{mode}.csv"'})
    return app

if __name__=='__main__':
    create_app().run(host='0.0.0.0',port=int(os.environ.get('PORT','5000')))
