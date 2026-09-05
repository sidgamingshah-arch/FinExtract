import json,time,urllib.request,urllib.error
BASE='http://127.0.0.1:8000/api/v1'
DOC='807485dc-b7b8-46e5-b867-133345be500f'
TPL='4b4e82d1-8f5d-497c-8306-628035e7b3a6'
ONT='f563d726-8788-4bef-9970-f071d1062194'
def call(path,method='GET',body=None,token=None):
 h={'Accept':'application/json'}
 if token:h['Authorization']='Bearer '+token
 data=None
 if body is not None:
  h['Content-Type']='application/json'; data=json.dumps(body).encode()
 req=urllib.request.Request(BASE+path,data=data,headers=h,method=method)
 with urllib.request.urlopen(req,timeout=120) as r:return json.loads(r.read() or b'{}')
tok=call('/auth/login','POST',{'username':'analyst'})['token']
started=call(f'/documents/{DOC}/extractions','POST',{
 'template_version_id':TPL,'ontology_version_id':ONT,'force':True
},tok)
run_id=started['run_id']; print('RUN_ID',run_id,'RULEBOOK',started['rulebook']['version'])
last=None
for _ in range(900):
 state=call(f'/extractions/{run_id}',token=tok)
 progress=state.get('progress') or {}; marker=(state.get('status'),progress.get('stage'),progress.get('stage_index'))
 if marker!=last:
  print('STATE',marker,'PCT',progress.get('pct')); last=marker
 if state.get('status') in {'succeeded','failed'}:
  print('FINAL',state.get('status'))
  print('LOG_TAIL',state.get('log_tail','')[-4000:])
  break
 time.sleep(2)
else: raise SystemExit('timeout')
