"""OS 파일 잠금. 프로세스 종료 시 자동 해제되므로 남은 lock 파일을 지울 필요가 없습니다."""
import os
from pathlib import Path
from .collector import CollectorError

class RunLock:
    def __init__(self,path): self.path=Path(path); self.file=None
    def __enter__(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.file=self.path.open('a+b')
        self.file.seek(0,os.SEEK_END)
        if self.file.tell()==0: self.file.write(b'0'); self.file.flush()
        self.file.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.file.close(); self.file=None
            raise CollectorError('ALREADY_RUNNING','같은 로그인 프로필 또는 이력을 사용하는 V5가 실행 중입니다.') from None
        return self
    def __exit__(self,*args):
        if self.file:
            try:
                self.file.seek(0)
                if os.name=='nt':
                    import msvcrt
                    msvcrt.locking(self.file.fileno(),msvcrt.LK_UNLCK,1)
                else:
                    import fcntl
                    fcntl.flock(self.file.fileno(),fcntl.LOCK_UN)
            finally: self.file.close(); self.file=None
