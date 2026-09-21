from __future__ import annotations
import importlib.util,json,uuid
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("remote",ROOT/"scripts/phase7_remote_control.py"); remote=importlib.util.module_from_spec(spec); spec.loader.exec_module(remote)

def config(root):
 return {"schema_version":f"{remote.SCHEMA}.config","instance":{"id":"future-id","provider":"AutoDL","region":"future-region"},"ssh":{"host":"future-host","port":22,"user":"root","host_fingerprint_sha256":"sha256:"+"a"*64},"repository":{"expected_commit":"a"*40,"remote_root":str(root),"upload_manifest_sha256":"sha256:"+"b"*64,"payload_manifest_path":str(root/"payload_manifest.json")},"python":{"executable":str(root/"python")},"experiment":{"preparation_root":str(root/"prep"),"run_root":str(root/"run"),"model_root":str(root/"model"),"integrity_evidence":str(root/"inventory.json"),"expected_freeze_sha256":"sha256:"+"c"*64,"profile":"high_gpu_bf16","max_generation_calls":60,"batch_wall_seconds":21600,"automatic_retry_count":0},"rental":{"hourly_price":"future","currency":"future","rental_time_cap_seconds":0,"cost_cap":"future"},"transfer":{"return_local_root":"future-local","exclude_patterns":["model weights","secrets"]},"claims":{"h1":False,"formal_quality":False},"approvals":{"manager_run_approved":True,"user_instance_started":True}}

_base_config=config
def config(root):
 c=_base_config(root); c["rental"]={"hourly_price":1,"currency":"CNY","rental_time_cap_seconds":21600,"cost_cap":6}; return c

class RemoteTests(TestCase):
 def setUp(self): self.root=ROOT/"outputs/phase7_autodl_prep_v1/remote-control-checks"/uuid.uuid4().hex/self._testMethodName; self.root.mkdir(parents=True)
 def test_config_rejects_experiment_drift(self):
  c=config(self.root); c["experiment"]["max_generation_calls"]=61
  with self.assertRaises(remote.ControlError): remote.validate_config(c)
 def test_no_gpu_preflight_does_not_probe_gpu_or_load_model(self):
  c=config(self.root)
  with patch.object(remote.subprocess,"run") as run:
   result=remote.preflight(c,"no-gpu")
  self.assertFalse(result["gpu"]["checked"]); run.assert_not_called()
 def test_launch_requires_exact_approval(self):
  c=config(self.root); c["approvals"]["manager_run_approved"]=False
  remote.validate_config(c)
  with patch.object(remote,"read",return_value=c),self.assertRaisesRegex(remote.ControlError,"approval missing"):
   remote.launch(self.root/"config.json",self.root/"launch")
 def test_package_and_validate_small_return(self):
  run=self.root/"result"; run.mkdir(); (run/"summary.json").write_text("{}"); remote.write_once(run/"batch_declaration.json",{"x":1}); remote.write_once(run/"row_terminal_inventory.json",{"rows":[{"i":i} for i in range(24)]})
  archive=self.root/"return.tar.gz"; result=remote.package_results(run,archive); checked=remote.validate_return(archive,Path(str(archive)+".manifest.json"))
  self.assertEqual(result["file_count"],3); self.assertEqual(checked["status"],"validated")
 def test_package_rejects_model_or_secret(self):
  run=self.root/"bad"; run.mkdir(); (run/"model.safetensors").write_bytes(b"x")
  with self.assertRaises(remote.ControlError): remote.package_results(run,self.root/"bad.tar.gz")
 def test_interrupt_is_single_signal_without_retry(self):
  evidence=self.root/"interrupt-v2"; evidence.mkdir(); remote.write_once(evidence/"launch.json",{"pid":123,"proc_start_id":"started"})
  with patch.object(remote,"_proc_start",return_value="started"),patch.object(remote.os,"kill") as kill: value=remote.interrupt(evidence)
  kill.assert_called_once_with(123,remote.signal.SIGTERM); self.assertEqual(value["status"],"interrupt_requested")
 def test_launch_command_uses_only_existing_experiment_entry(self):
  root=self.root/"v2"; root.mkdir(); c=config(root); cp=root/"cfg.json"; cp.write_text(json.dumps(c))
  fake=type("P",(),{"pid":77})()
  with patch.object(remote.sys,"platform","linux"),patch.object(remote,"ROOT",root),patch.object(remote,"_proc_start",return_value="started"),patch.object(remote,"preflight",return_value={"ready":True}),patch.object(remote.subprocess,"Popen",return_value=fake) as popen:
   remote.launch(cp,root/"launch")
  cmd=popen.call_args.args[0]; self.assertTrue(any(str(x).endswith("phase7_experiment1.py") for x in cmd)); self.assertTrue(any(str(x).endswith("phase7_autodl_launch.sh") for x in cmd))

 def test_supervisor_records_verified_normal_exit(self):
  root=self.root/"supervisor-normal"; root.mkdir()
  class P:
   pid=88
   def poll(self): return 0
   def wait(self,timeout=None): return 0
  with patch.object(remote,"_enable_subreaper"),patch.object(remote,"_cleanup_owned",return_value={"verified":True}),patch.object(remote.subprocess,"Popen",return_value=P()),patch.object(remote,"_proc_start",return_value="start"),patch.object(remote.signal,"signal",return_value=None):
   self.assertEqual(remote.supervise("python","runner","prep","run","model","integrity",root),0)
  self.assertTrue(remote.read(root/"exit.json")["cleanup_verified"])

 def test_supervisor_timeout_requests_grace_then_owned_cleanup(self):
  root=self.root/"supervisor-timeout-v3"; root.mkdir()
  class P:
   pid=99; done=False
   def poll(self): return -9 if self.done else None
   def wait(self,timeout=None): self.done=True; return -9
  proc=P()
  def cleanup(child,tracked): child.done=True; return {"verified":True}
  with patch.object(remote,"_enable_subreaper"),patch.object(remote,"_cleanup_owned",side_effect=cleanup) as owned,patch.object(remote.subprocess,"Popen",return_value=proc),patch.object(remote,"_proc_start",return_value="start"),patch.object(remote.signal,"signal",return_value=None),patch.object(remote.os,"kill") as kill,patch.object(remote.time,"sleep"),patch.object(remote.time,"monotonic",side_effect=[0,21601,21601,21662]):
   remote.supervise("python","runner","prep","run","model","integrity",root)
  kill.assert_called_once_with(99,remote.signal.SIGINT); owned.assert_called_once()

 def test_cleanup_collects_adopted_separate_session_not_reused_pid(self):
  class P:
   pid=90
   def poll(self): return 0
   def wait(self,timeout=None): return 0
  gone=set()
  def info(pid):
   if pid==90 or pid in gone: return None
   return {"state":"S","ppid":remote.os.getpid(),"start":"adopted" if pid==91 else "reused"}
  def kill(pid,sig): gone.add(pid)
  tracked={90:"parent",91:"adopted",92:"old-identity"}
  with patch.object(remote,"_descendants",return_value={}),patch.object(remote,"_proc_info",side_effect=info),patch.object(remote.os,"kill",side_effect=kill) as signal_call:
   result=remote._cleanup_owned(P(),tracked)
  self.assertTrue(result["verified"]); signal_call.assert_called_once_with(91,getattr(remote.signal,"SIGKILL",9))

 def test_interrupt_rejects_absent_or_reused_identity(self):
  evidence=self.root/"stale"; evidence.mkdir(); remote.write_once(evidence/"launch.json",{"pid":123,"proc_start_id":"old"})
  with patch.object(remote,"_proc_start",return_value="new"),patch.object(remote.os,"kill") as kill,self.assertRaises(remote.ControlError): remote.interrupt(evidence)
  kill.assert_not_called()

 def test_return_includes_only_explicit_bound_launch_evidence(self):
  run=self.root/"run"; run.mkdir(); remote.write_once(run/"batch_declaration.json",{"x":1}); remote.write_once(run/"row_terminal_inventory.json",{"rows":[{}]*24})
  evidence=self.root/"launch"; evidence.mkdir(); remote.write_once(evidence/"config.json",{"experiment":{"run_root":str(run)}}); remote.write_once(evidence/"exit.json",{"cleanup_verified":True}); (evidence/"unrelated.txt").write_text("not returned")
  archive=self.root/"return.tar.gz"; remote.package_results(run,archive,evidence)
  self.assertEqual(remote.validate_return(archive,Path(str(archive)+".manifest.json"))["file_count"],4)
