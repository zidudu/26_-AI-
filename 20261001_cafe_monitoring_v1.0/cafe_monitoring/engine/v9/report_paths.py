"""Names for newly generated reports. Existing artifact paths remain authoritative."""
from datetime import datetime
from pathlib import Path
from v8.configuration import KST


def monitoring_path(folder, *, created_at=None):
    """Use the generation date in Korea, even on a UTC-configured host.

    Each execution/rebuild already has its own directory. Call once per render
    and use the returned path for saving, validation, reporting and attachment.
    """
    created_at = created_at or datetime.now(KST)
    if created_at.tzinfo is None or created_at.utcoffset() is None:
        raise ValueError('PPT generation time must include a time zone')
    date = created_at.astimezone(KST).strftime('%Y%m%d')
    return Path(folder) / (date + '_monitoring.pptx')
