from enum import Enum
class Role(str, Enum):
    admin = 'admin'
    analyst = 'analyst'
    viewer = 'viewer'
class JobStatus(str, Enum):
    queued = 'queued'
    running = 'running'
    completed = 'completed'
    failed = 'failed'
class RequestResult(str, Enum):
    borrowed = 'borrowed'   # 借成
    shortage = 'shortage'   # 未借成但报缺车
class SettlementStatus(str, Enum):
    running = 'running'
    done = 'done'
class SettlementItemStatus(str, Enum):
    settled = 'settled'
    held = 'held'
