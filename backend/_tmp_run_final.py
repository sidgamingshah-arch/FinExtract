import json,time,urllib.request,sqlite3
BASE='http://127.0.0.1:8000/api/v1'; DOC='807485dc-b7b8-46e5-b867-133345be500f'
def call(path,method='GET',body=None,token=None):
 h={'Accept':'application/json'}; data=None
 if token:h['Authorization']='Bearer '+token
 if body is not None:h['Content-Type']='application/json'; data=json.dumps(body).encode()
 req=urllib.request.Request(BASE+path,data=data,headers=h,method=method)
 with urllib.request.urlopen(req,timeout=120) as r:return json.loads(r.read() or b'{}')
con=sqlite3.connect('finex.db'); con.row_factory=sqlite3.Row; cur=con.cursor()
tpl=cur.execute("select id,version from template_versions where template_key='output_csv_hk_v1' order by version desc limit 1").fetchone()
ont=cur.execute("select id,version from ontology_versions where ontology_key='output_csv_hk' order by version desc limit 1").fetchone(); con.close()
tok=call('/auth/login','POST',{'username':'analyst'})['token']
started=call(f'/documents/{DOC}/extractions','POST',{'template_version_id':tpl['id'],'ontology_version_id':ont['id'],'force':True},tok)
run_id=started['run_id']; print('RUN_ID',run_id,'TEMPLATE',tpl['version'],'ONTOLOGY',ont['version'],flush=True)
last=None
for _ in range(600):
 s=call(f'/extractions/{run_id}',token=tok); p=s.get('progress') or {}
 marker=(s.get('status'),p.get('stage'),p.get('stage_index'))
 if marker!=last: print('STATE',marker,'PCT',p.get('pct'),flush=True); last=marker
 if s.get('status') in {'succeeded','failed'}:
  print('FINAL',s.get('status'),flush=True); print((s.get('log_tail') or '')[-6000:],flush=True); break
 time.sleep(2)
else: raise SystemExit('timeout')
