"""Windows 자식 프로세스의 수명 제어. Windows 이외에서는 사용하지 않습니다."""
import os

def windows_job(proc):
    """래퍼가 종료되면 그 자식도 종료하는 Windows Job. 다른 프로세스에는 적용하지 않음."""
    if os.name!='nt':return None
    import ctypes
    from ctypes import wintypes as w
    class BASIC(ctypes.Structure):
        _fields_=[('PerProcessUserTimeLimit',ctypes.c_longlong),('PerJobUserTimeLimit',ctypes.c_longlong),
                  ('LimitFlags',w.DWORD),('MinimumWorkingSetSize',ctypes.c_size_t),('MaximumWorkingSetSize',ctypes.c_size_t),
                  ('ActiveProcessLimit',w.DWORD),('Affinity',ctypes.c_size_t),('PriorityClass',w.DWORD),('SchedulingClass',w.DWORD)]
    class IOC(ctypes.Structure):
        _fields_=[(x,ctypes.c_ulonglong) for x in ('ReadOperationCount','WriteOperationCount','OtherOperationCount','ReadTransferCount','WriteTransferCount','OtherTransferCount')]
    class EXT(ctypes.Structure):
        _fields_=[('BasicLimitInformation',BASIC),('IoInfo',IOC),('ProcessMemoryLimit',ctypes.c_size_t),
                  ('JobMemoryLimit',ctypes.c_size_t),('PeakProcessMemoryUsed',ctypes.c_size_t),('PeakJobMemoryUsed',ctypes.c_size_t)]
    k=ctypes.WinDLL('kernel32',use_last_error=True)
    k.CreateJobObjectW.argtypes=[ctypes.c_void_p,w.LPCWSTR];k.CreateJobObjectW.restype=w.HANDLE
    k.SetInformationJobObject.argtypes=[w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD];k.SetInformationJobObject.restype=w.BOOL
    k.AssignProcessToJobObject.argtypes=[w.HANDLE,w.HANDLE];k.AssignProcessToJobObject.restype=w.BOOL
    k.CloseHandle.argtypes=[w.HANDLE];k.CloseHandle.restype=w.BOOL
    handle=k.CreateJobObjectW(None,None)
    if not handle:raise OSError(ctypes.get_last_error(),'CreateJobObject failed')
    info=EXT();info.BasicLimitInformation.LimitFlags=0x2000 # KILL_ON_JOB_CLOSE
    if not k.SetInformationJobObject(handle,9,ctypes.byref(info),ctypes.sizeof(info)) or not k.AssignProcessToJobObject(handle,w.HANDLE(proc._handle)):
        err=ctypes.get_last_error();k.CloseHandle(handle);raise OSError(err,'Could not supervise cloudflared')
    return lambda:k.CloseHandle(handle)

