import sqlite3, json, time
run_id='2025111900987-20260827-145354'
con=sqlite3.connect('finex.db'); con.row_factory=sqlite3.Row; cur=con.cursor()
for i in range(240):
    r=cur.execute('select status,progress,logs,result,ontology_version_id from extraction_runs where id=?',(run_id,)).fetchone()
    st=(r['status'] or '').lower()
    pr=r['progress'] or {}
    if isinstance(pr,str):
        try: pr=json.loads(pr)
        except Exception: pr={}
    print('POLL',i,'STATUS',st,'STAGE',pr.get('stage'),'PCT',pr.get('pct'))
    if st!='running':
        res=r['result']
        print('DONE_STATUS',st)
        print('HAS_RESULT', res is not None)
        break
    time.sleep(2)
else:
    print('TIMEOUT_WAITING')
con.close()
