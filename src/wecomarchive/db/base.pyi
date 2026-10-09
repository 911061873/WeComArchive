from _typeshed import Incomplete
from sqlalchemy.pool import ConnectionPoolEntry as ConnectionPoolEntry

class Database:
    lock: Incomplete
    engine: Incomplete
    session: Incomplete
    def __init__(self, url: str) -> None: ...
    def initialize(self) -> None:
        """空库自动安装；已有库只检查版本，绝不隐式升级。"""
    def dispose(self) -> None: ...
    def acquire_instance(self) -> None:
        """锁在构造阶段获取，避免其他实例恢复本实例正在处理的任务。"""
    def release_instance(self) -> None: ...
