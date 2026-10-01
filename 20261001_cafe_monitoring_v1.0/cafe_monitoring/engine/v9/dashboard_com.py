"""Bounded recovery of explicitly rejected Office dispatch operations.

Only RPC_E_CALL_REJECTED / RPC_E_SERVERCALL_RETRYLATER are retried.
Attribute errors, validation failures and a disconnected server are not.
The callback is one dispatch operation, never a whole graph or slide.
"""
import time
from pathlib import Path

BUSY_HRESULTS={0x80010001,0x8001010A}
RETRY_DELAYS=(.1,.2,.4,.8,1.0)


def hresult(exc):
    value=getattr(exc,'hresult',None)
    if value is None and exc.args and type(exc.args[0]) is int:value=exc.args[0]
    return value if type(value) is int else None


def pump_messages():
    try:import pythoncom
    except ImportError:return
    pythoncom.PumpWaitingMessages()


class Guard:
    def __init__(self,audit=None,phase='dashboard'):
        self.audit=audit if audit is not None else []
        self.phase=phase;self.calls=0;self.retries=0;self.pump_warned=False

    def pump(self):
        try:pump_messages()
        except Exception as exc:
            if not self.pump_warned:
                self.audit.append({'phase':self.phase,'operation':'message_pump','error':str(exc)})
                self.pump_warned=True

    def call(self,operation,callback):
        from v9.dashboard_chart import error_record
        self.calls+=1
        if self.calls%64==0:self.pump()
        event=None
        for attempt in range(len(RETRY_DELAYS)+1):
            try:
                result=callback()
                if event is not None:event['recovered']=True
                return result
            except Exception as exc:
                code=hresult(exc)
                retryable=code is not None and (code & 0xffffffff) in BUSY_HRESULTS
                if event is None:
                    event={'phase':self.phase,'operation':operation,'errors':[],
                           'retries':0,'recovered':False}
                    self.audit.append(event)
                event['errors'].append(error_record(exc))
                if not retryable or attempt==len(RETRY_DELAYS) or self.retries>=40:
                    event['stopped']='not_retryable' if not retryable else 'retry_limit'
                    raise
                delay=RETRY_DELAYS[attempt];self.retries+=1;event['retries']+=1
                print(f'[Office 호출 대기] {self.phase} / {operation} / '
                      f'0x{code & 0xffffffff:08X} / {attempt+1}/{len(RETRY_DELAYS)}',flush=True)
                self.pump();time.sleep(delay)

    def wrap(self,value,path='deck'):
        if isinstance(value,Proxy):
            return value if value._guard is self else Proxy(value._target,self,path)
        if value is None or isinstance(value,(str,bytes,int,float,bool,tuple,list,dict)):return value
        return Proxy(value,self,path)


def unwrap(value):
    if isinstance(value,Proxy):return value._target
    return value


def write_audits(folder,report,primary_error=None,include_trial=False):
    """Persist cached Python data only; preserve a pending export exception."""
    from v754.core import write_json
    entries=[('dashboard_chart_audit.json',report.get('dashboard_chart_audit',[])),
             ('dashboard_com_audit.json',report.get('dashboard_com_audit',[]))]
    if include_trial:entries.append(('chart_trial_report.json',report))
    first=None
    for name,data in entries:
        try:write_json(Path(folder)/name,data)
        except Exception as exc:
            if first is None:first=exc
            print(f'[요약 진단 저장 경고] {name} / {exc}',flush=True)
    if first is not None and primary_error is None:raise first


class Proxy:
    """Delegate a single get/set/call while preserving its underlying target."""
    def __init__(self,target,guard,path):
        object.__setattr__(self,'_target',target)
        object.__setattr__(self,'_guard',guard)
        object.__setattr__(self,'_path',path)

    def __getattr__(self,name):
        path=self._path+'.'+name
        value=self._guard.call('get '+path,lambda:getattr(self._target,name))
        return self._guard.wrap(value,path)

    def __setattr__(self,name,value):
        self._guard.call('set '+self._path+'.'+name,lambda:setattr(self._target,name,unwrap(value)))

    def __call__(self,*args,**kwargs):
        args=tuple(unwrap(v) for v in args);kwargs={k:unwrap(v) for k,v in kwargs.items()}
        value=self._guard.call('call '+self._path,lambda:self._target(*args,**kwargs))
        return self._guard.wrap(value,self._path+'()')
