import asyncio,json,os,signal,sys,time
from pathlib import Path
from livekit import api
from voiceops.ledger import CallLedger

ROOT=Path(__file__).resolve().parents[1]
V=Path(os.environ.get('VOICEOPS_EVIDENCE','.runtime/evidence')).resolve();V.mkdir(parents=True,exist_ok=True)
ENV=dict(os.environ,PYTHONPATH=str(ROOT))
KEY='local-demo-secret-voice-operations-123456'

async def verify(mode):
 room='voiceops-worker-'+mode+'-'+str(time.time_ns())
 db=V/(room+'.sqlite')
 lk=api.LiveKitAPI('http://127.0.0.1:18140','devkey',KEY)
 trunk=await lk.sip.create_sip_inbound_trunk(api.CreateSIPInboundTrunkRequest(trunk=api.SIPInboundTrunkInfo(name=room,numbers=['2000'],auth_username='voiceops',auth_password='local-pass')))
 rule=await lk.sip.create_sip_dispatch_rule(api.CreateSIPDispatchRuleRequest(name=room,trunk_ids=[trunk.sip_trunk_id],rule=api.SIPDispatchRule(dispatch_rule_direct=api.SIPDispatchRuleDirect(room_name=room))))
 processes=[];logs=[];results={'scenario':mode}
 async def worker():
  f=(V/(room+'-worker-'+str(len(logs))+'.log')).open('wb');logs.append(f)
  p=await asyncio.create_subprocess_exec(sys.executable,'-m','voiceops.worker','--database',str(db),'--room',room,'--media-timeout','5','--allow-test-faults',cwd=ROOT,env=ENV,stdout=asyncio.subprocess.PIPE,stderr=f);processes.append(p)
  line=await asyncio.wait_for(p.stdout.readline(),20)
  assert line,line
  results.setdefault('workers',[]).append(json.loads(line))
  return p
 try:
  w=await worker()
  f=(V/(room+'-phone.log')).open('wb');logs.append(f)
  phone=await asyncio.create_subprocess_exec(os.environ.get('SIP_FIXTURE',str(ROOT/'fixtures/call')),'finish' if mode=='natural-drain' else 'hold',env=ENV,stdout=asyncio.subprocess.PIPE,stderr=f);processes.append(phone)
  ready=await asyncio.wait_for(phone.stdout.readline(),15);assert json.loads(ready)['stage']=='ready'
  rows=CallLedger(db).list_calls('local-team');assert len(rows)==1
  results['call_id']=rows[0]['id'];results['initial_status']=rows[0]['status']
  start=time.monotonic()
  if mode=='restart':
   w.kill();await w.wait();w=await worker()
  elif mode in {'natural-drain','deadline-drain'}:w.send_signal(signal.SIGTERM)
  elif mode=='media-timeout':w.send_signal(signal.SIGUSR1)
  out,_=await asyncio.wait_for(phone.communicate(),20)
  assert phone.returncode==0,out
  results['phone']=json.loads(out.splitlines()[-1]);results['elapsed_ms']=round((time.monotonic()-start)*1000)
  for _ in range(30):
   row=CallLedger(db).get(results['call_id'],'local-team')
   if row['status']=='ended':break
   await asyncio.sleep(.1)
  assert row['status']=='ended',row
  results['final_status']=row['status']
  results['events']=CallLedger(db).events(row['id'],'local-team')
  if w.returncode is None and mode not in {'natural-drain','deadline-drain'}:w.send_signal(signal.SIGTERM)
  await asyncio.wait_for(w.wait(),15)
  assert w.returncode==0,w.returncode
  return results
 finally:
  for p in processes:
   if p.returncode is None:p.kill();await p.wait()
  for f in logs:f.close()
  await lk.sip.delete_sip_dispatch_rule(api.DeleteSIPDispatchRuleRequest(sip_dispatch_rule_id=rule.sip_dispatch_rule_id))
  await lk.sip.delete_sip_trunk(api.DeleteSIPTrunkRequest(sip_trunk_id=trunk.sip_trunk_id))
  await lk.room.delete_room(api.DeleteRoomRequest(room=room))
  await lk.aclose()
async def main():
 result=[]
 for mode in ['natural-drain','deadline-drain','restart','media-timeout']:
  value=await verify(mode);result.append(value);print(json.dumps(value),flush=True)
 (V/'worker-runtime-evidence.json').write_text(json.dumps(result,indent=2)+'\n')
asyncio.run(main())
