import asyncio,json,os,time
import argparse
from pathlib import Path
from livekit import api,rtc

async def one(mode):
 p=Path(os.environ.get('VOICEOPS_EVIDENCE','.runtime/evidence'));p.mkdir(parents=True,exist_ok=True);room_name='voiceops-transfer-'+mode
 lk=api.LiveKitAPI('http://127.0.0.1:18140',os.environ.get('LIVEKIT_API_KEY','devkey'),os.environ.get('LIVEKIT_API_SECRET','local-demo-secret-voice-operations-123456'))
 trunk=await lk.sip.create_sip_inbound_trunk(api.CreateSIPInboundTrunkRequest(trunk=api.SIPInboundTrunkInfo(name='voiceops-local',numbers=['2000'],auth_username='voiceops',auth_password='local-pass')))
 rule=await lk.sip.create_sip_dispatch_rule(api.CreateSIPDispatchRuleRequest(name='voiceops-local',trunk_ids=[trunk.sip_trunk_id],rule=api.SIPDispatchRule(dispatch_rule_direct=api.SIPDispatchRuleDirect(room_name=room_name))))
 room=rtc.Room();source=rtc.AudioSource(8000,1);track=rtc.LocalAudioTrack.create_audio_track('verified-echo',source);evidence={'frames':0,'samples':0,'dtmf':[]};tasks=[]
 async def echo(remote):
  async for event in rtc.AudioStream(remote,sample_rate=8000,num_channels=1):
   evidence['frames']+=1;evidence['samples']+=event.frame.samples_per_channel
   await source.capture_frame(event.frame)
 @room.on('track_subscribed')
 def subscribed(remote,pub,participant):
  if remote.kind==rtc.TrackKind.KIND_AUDIO:tasks.append(asyncio.create_task(echo(remote)))
 @room.on('sip_dtmf_received')
 def dtmf(event):evidence['dtmf'].append(str(event))
 token=api.AccessToken(os.environ.get('LIVEKIT_API_KEY','devkey'),os.environ.get('LIVEKIT_API_SECRET','local-demo-secret-voice-operations-123456')).with_identity('echo-worker').with_grants(api.VideoGrants(room_join=True,room=room_name)).to_jwt()
 try:
  await room.connect('ws://127.0.0.1:18140',token)
  await room.local_participant.publish_track(track,rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))
  env=os.environ.copy()
  error_log=(p/('transfer-'+mode+'.log')).open('wb')
  proc=await asyncio.create_subprocess_exec(os.environ.get('SIP_FIXTURE','fixtures/call'),mode,env=env,stdout=asyncio.subprocess.PIPE,stderr=error_log)
  first=await asyncio.wait_for(proc.stdout.readline(),15)
  ready=json.loads(first)
  participants=await lk.room.list_participants(api.ListParticipantsRequest(room=room_name))
  sip=next(x for x in participants.participants if x.identity!='echo-worker')
  evidence['sip_participant']=sip.identity
  evidence['attributes']=dict(sip.attributes)
  start=time.perf_counter()
  try:
   await asyncio.wait_for(lk.sip.transfer_sip_participant(api.TransferSIPParticipantRequest(room_name=room_name,participant_identity=sip.identity,transfer_to='sip:'+mode+'@127.0.0.1:18160')),25)
   evidence['api_result']='ok'
  except Exception as exc: evidence['api_result']=type(exc).__name__+': '+str(exc)
  evidence['api_latency_ms']=round((time.perf_counter()-start)*1000)
  print(json.dumps(evidence),flush=True)
  out,err=await asyncio.wait_for(proc.communicate(),20)
  error_log.close()
  out=first+out
  evidence.update({'returncode':proc.returncode,'client':out.decode(),'trunk_id':trunk.sip_trunk_id,'dispatch_id':rule.sip_dispatch_rule_id})
  print(json.dumps(evidence),flush=True)
 finally:
  if 'proc' in locals() and proc.returncode is None:
   proc.kill();await proc.wait()
  if 'error_log' in locals():error_log.close()
  for task in tasks:task.cancel()
  await room.disconnect()
  await lk.sip.delete_sip_dispatch_rule(api.DeleteSIPDispatchRuleRequest(sip_dispatch_rule_id=rule.sip_dispatch_rule_id))
  await lk.sip.delete_sip_trunk(api.DeleteSIPTrunkRequest(sip_trunk_id=trunk.sip_trunk_id))
  await lk.aclose()
 (p/('transfer-'+mode+'.json')).write_text(json.dumps(evidence,indent=2)+'\n')
 assert evidence['returncode']==0 and evidence['frames']>0 and evidence['dtmf'],evidence

async def main():
 for mode in ['operator','reject','unanswered']:await one(mode)
asyncio.run(main())
