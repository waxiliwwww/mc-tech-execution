"""Linux worker ownership across child sessions; never signal by name or user."""
from __future__ import annotations
import ctypes
import os
from pathlib import Path
import signal
import time


class ProcessControlError(ValueError):pass


class OwnedProcessTree:
    def __init__(self):
        if not hasattr(os,'pidfd_open') or not hasattr(signal,'pidfd_send_signal'):
            raise ProcessControlError('本地作业需要支持pidfd的Linux/WSL2；未启动计算')
        self.pid=os.getpid()
        self.boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        libc=ctypes.CDLL(None,use_errno=True)
        flag=ctypes.c_int()
        if libc.prctl(36,1,0,0,0) or libc.prctl(37,ctypes.byref(flag),0,0,0) or flag.value!=1:
            raise ProcessControlError('无法启用Linux子进程收养；未启动计算')
        try:
            fd=os.pidfd_open(self.pid);os.close(fd)
        except OSError as error:
            raise ProcessControlError('内核不支持本作业所需pidfd；未启动计算') from error

    def info(self,pid):
        try:
            fields=Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()
            return {'pid':pid,'ppid':int(fields[1]),'state':fields[0],
                    'identity':self.boot+':'+fields[19]}
        except (FileNotFoundError,ProcessLookupError,PermissionError):return None

    def direct_children(self):
        return {int(pid) for pid in Path(f'/proc/{self.pid}/task/{self.pid}/children').read_text().split()}

    def descendants(self):
        # /proc is not atomic. Re-read current direct children as anchors after
        # scanning; adoption must never look like an empty descendant graph.
        deadline=time.monotonic()+.25
        while True:
            records={}
            for path in Path('/proc').iterdir():
                if path.name.isdecimal():
                    value=self.info(int(path.name))
                    if value:records[value['pid']]=value
            anchors=self.direct_children();current={pid:self.info(pid) for pid in anchors}
            consistent=all(value and value['ppid']==self.pid for value in current.values())
            if consistent:
                records.update(current);owned={self.pid};found={}
                while True:
                    additional={pid:value for pid,value in records.items()
                                if value['ppid'] in owned and pid not in owned}
                    if not additional:break
                    found.update(additional);owned.update(additional)
                if found or not self.direct_children():return found
            if time.monotonic()>=deadline:
                raise ProcessControlError('子进程收养状态持续变化，未确认清理完成')
            time.sleep(.005)

    def is_descendant(self,pid):
        seen=set()
        while pid not in seen:
            seen.add(pid);value=self.info(pid)
            if not value:return False
            if value['ppid']==self.pid:return True
            pid=value['ppid']
            if pid<=1:return False
        return False

    def kill_owned(self,value):
        if value['pid']==self.pid:raise ProcessControlError('拒绝向作业工作进程自身发送信号')
        try:fd=os.pidfd_open(value['pid'])
        except ProcessLookupError:return False
        try:
            current=self.info(value['pid'])
            if not current or current['identity']!=value['identity'] or not self.is_descendant(value['pid']):return False
            signal.pidfd_send_signal(fd,signal.SIGKILL)
            return True
        except ProcessLookupError:return False
        finally:os.close(fd)

    def stop(self,runner,kill_runner=True,timeout=5):
        # Cleanup errors are a persistent unknown outcome, never a second
        # exception that prevents the worker from recording its terminal state.
        try:return self._stop(runner,kill_runner,timeout)
        except (OSError,ProcessControlError) as error:
            known=[]
            try:
                known=[value for pid in self.direct_children() if (value:=self.info(pid))]
            except OSError:pass
            return {'verified_empty':False,'killed':[],'unexpected_descendants':[],
                    'remaining':known,'error':type(error).__name__+': '+str(error)}

    def _stop(self,runner,kill_runner=True,timeout=5):
        """Drain actual descendants, reaping adopted children but not Popen's runner."""
        killed={};initial=self.descendants()
        unexpected=[v for p,v in initial.items() if p!=runner.pid and v['state']!='Z']
        if kill_runner and runner.poll() is None:
            value=self.info(runner.pid)
            if value and self.kill_owned(value):killed[runner.pid]=value
        deadline=time.monotonic()+timeout
        while True:
            records=self.descendants()
            for pid,value in records.items():
                if pid==runner.pid:
                    if runner.poll() is None and (kill_runner or killed):
                        if self.kill_owned(value):killed[pid]=value
                    continue
                if value['state']!='Z' and self.kill_owned(value):killed[pid]=value
            # Only the Popen object reaps runner, preserving its real exit code.
            runner.poll()
            for pid,value in records.items():
                if pid!=runner.pid and value['ppid']==self.pid:
                    try:os.waitpid(pid,os.WNOHANG)
                    except ChildProcessError:pass
            remaining=self.descendants()
            if not remaining:return {'verified_empty':True,'killed':list(killed.values()),
                                      'unexpected_descendants':unexpected,'remaining':[]}
            if time.monotonic()>=deadline:
                return {'verified_empty':False,'killed':list(killed.values()),
                        'unexpected_descendants':unexpected,'remaining':list(remaining.values())}
            time.sleep(.025)
