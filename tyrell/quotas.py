"""Provider-reported account quotas; never infer allowance from token usage."""
from datetime import datetime
import math
import time


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def period(minutes):
    if not number(minutes) or minutes <= 0:
        return 'window'
    if minutes % 1440 == 0:
        return '%gd' % (minutes / 1440)
    if minutes % 60 == 0:
        return '%gh' % (minutes / 60)
    return '%gm' % minutes


def codex_quota(result):
    buckets = result.get('rateLimitsByLimitId')
    if not isinstance(buckets, dict) or not buckets:
        snapshot = result.get('rateLimits') or {}
        buckets = {snapshot.get('limitId') or 'codex': snapshot}
    rows = []
    for key, bucket in buckets.items():
        if not isinstance(bucket, dict):
            continue
        for field in ('primary', 'secondary'):
            window = bucket.get(field)
            if not isinstance(window, dict) or not number(window.get('usedPercent')):
                continue
            label = period(window.get('windowDurationMins'))
            if key != 'codex':
                label = str(key) + ' ' + label
            reset = window.get('resetsAt')
            rows.append({'label': label, 'remaining': max(0, min(100, 100-window['usedPercent'])),
                         'reset': reset if number(reset) else None})
    return {'rows': rows, 'checkedAt': time.time()}


def copilot_quota(result):
    rows = []
    snapshots = result.get('quotaSnapshots') or {}
    if not isinstance(snapshots, dict):
        return {'rows': [], 'checkedAt': time.time()}
    for key, value in sorted(snapshots.items(), key=lambda item: item[0] != 'premium_interactions'):
        if not isinstance(value, dict):
            continue
        unlimited = value.get('isUnlimitedEntitlement') is True or value.get('entitlementRequests') == -1
        remaining = value.get('remainingPercentage')
        if not unlimited and not number(remaining):
            continue
        reset = None
        try:
            reset = datetime.fromisoformat(value.get('resetDate', '').replace('Z', '+00:00')).timestamp()
        except (TypeError, ValueError, AttributeError, OverflowError):
            pass
        rows.append({'label': 'premium' if key == 'premium_interactions' else str(key),
                     'remaining': None if unlimited else max(0, min(100, remaining)),
                     'unlimited': unlimited, 'reset': reset,
                     'overage': value.get('usageAllowedWithExhaustedQuota') is True
                                or value.get('overageAllowedWithExhaustedQuota') is True})
    return {'rows': rows, 'checkedAt': time.time()}


def quota_label(info, detail=False):
    quota = info.get('quota') or {}
    rows = quota.get('rows') or []
    if not rows:
        return 'Quota unavailable'
    stale = time.time() - quota.get('checkedAt', 0) > 180
    parts = []
    if not detail and any(not row.get('unlimited') for row in rows):
        rows = [row for row in rows if not row.get('unlimited')]
    for row in rows:
        reset = row.get('reset')
        expired = number(reset) and reset <= time.time()
        amount = 'Unlimited' if row.get('unlimited') else '%g%% left' % row['remaining']
        if expired and not row.get('unlimited'):
            amount += ' (reset due)'
        label = row['label'] + ' ' + amount
        if detail and number(reset):
            try:
                label += ' · resets ' + datetime.fromtimestamp(reset).strftime('%d %b %H:%M')
            except (OverflowError, OSError, ValueError):
                pass
        if detail and row.get('overage'):
            label += ' · additional usage allowed'
        parts.append(label)
    return ('Stale · ' if stale else '') + ' / '.join(parts)
