"""Process-scoped data-root lock, automatically released by the OS on crash."""
import os
from pathlib import Path

class WorkerLock:
    def __init__(self,root):
        path=Path(root)/'cache'/'worker.lock';path.parent.mkdir(parents=True,exist_ok=True)
        self.stream=path.open('a+b');self.stream.seek(0)
        if path.stat().st_size==0:self.stream.write(b'0');self.stream.flush()
        self.stream.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self.stream.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.stream.close()
            raise RuntimeError('该数据目录已有后台进程，请使用现有实例或独立数据目录') from None
    def close(self):
        if not self.stream.closed:self.stream.close()
    def __enter__(self):return self
    def __exit__(self,*args):self.close()
