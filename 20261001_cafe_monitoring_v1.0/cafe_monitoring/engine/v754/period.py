"""한국시간의 [시작, 종료) 범위. 목록 날짜는 후보 탐색에만 사용합니다."""
from dataclasses import dataclass
from datetime import datetime, timedelta
import re
from .core import V7Error
from .collect.collector import KST


def timestamp(value):
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            raise ValueError()
        return dt.astimezone(KST)
    except (TypeError, ValueError):
        raise V7Error('INVALID_TIMESTAMP', '시간대가 포함된 작성 시각을 확인하지 못했습니다.') from None


def input_time(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}', value.strip()):
        raise V7Error('INVALID_PERIOD', 'YYYY-MM-DD HH:MM 형식으로 입력하세요. 모든 입력은 한국시간입니다.')
    try:
        return datetime.fromisoformat(value.strip()).replace(tzinfo=KST)
    except ValueError:
        raise V7Error('INVALID_PERIOD', '존재하는 날짜와 시각을 입력하세요.') from None


@dataclass(frozen=True)
class Window:
    start: datetime
    end: datetime

    def __post_init__(self):
        if (self.start.tzinfo is None or self.end.tzinfo is None or self.start >= self.end
                or any(d.second or d.microsecond for d in (self.start, self.end))):
            raise V7Error('INVALID_PERIOD', '시작은 종료보다 빨라야 하며 분 단위로 지정해야 합니다.')

    def contains(self, written_at):
        value = timestamp(written_at)
        return self.start <= value < self.end

    def day_relation(self, day):
        """날짜만 있는 목록에서 시각을 00:00으로 단정해 글을 제외하지 않습니다."""
        lo = datetime.combine(day, datetime.min.time(), tzinfo=KST)
        hi = lo + timedelta(days=1)
        if hi <= self.start:
            return 'older'
        if lo >= self.end:
            return 'newer'
        return 'candidate'

    def record(self):
        return {'start': self.start.isoformat(), 'end': self.end.isoformat(),
                'timezone': 'Asia/Seoul', 'rule': 'start_inclusive_end_exclusive',
                'input_precision': 'minute', 'basis': 'article_written_at'}

    @classmethod
    def from_record(cls, record):
        if not isinstance(record, dict) or record.get('rule') != 'start_inclusive_end_exclusive':
            raise V7Error('INVALID_PERIOD', '저장된 기간 판정 기준을 확인하지 못했습니다.')
        return cls(timestamp(record.get('start')), timestamp(record.get('end')))

    @classmethod
    def from_input(cls, start, end, now=None):
        window = cls(input_time(start), input_time(end))
        now = now or datetime.now(KST)
        if window.end > now:
            raise V7Error('FUTURE_PERIOD', '종료 시각은 현재 시각 이후로 지정할 수 없습니다.')
        return window
