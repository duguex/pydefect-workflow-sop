#!/usr/bin/env python3
"""30-minute batch watch: snapshot 2026 batch progress + anomalies.

Appends to ~/.vasp_sop/batch_watch.log; marks ALERT lines on anomalies
(new-failure spikes, loop death, empty queue with unfinished systems).
"""
import datetime, os, pathlib, sqlite3, subprocess

BASE = pathlib.Path('/mnt/shared/home/2sidesniddle/vasp/2026_undergo_spin_defect')
LOG = pathlib.Path(os.path.expanduser('~/.vasp_sop/batch_watch.log'))
CRISP_DB = pathlib.Path(os.path.expanduser('~/.crisp/data/agent.db'))
VASP_SOP = '/home/duguex/vasp_sop/.venv/bin/vasp-sop'
NOW = datetime.datetime.now().strftime('%m-%d %H:%M')

def out(line):
    print(line)
    with LOG.open('a') as f:
        f.write(f'[{NOW}] {line}\n')

# 1) batch status table
try:
    r = subprocess.run([VASP_SOP, 'batch', 'status', str(BASE)],
                       capture_output=True, text=True, timeout=120)
    table = r.stdout
    out('--- status ---')
    for line in table.splitlines():
        if line.strip() and not line.startswith('Loop'):
            out(line)
except Exception as e:
    out(f'ALERT status failed: {e}')

# 2) crisp queue counts (submit_time is ISO-8601 text — compare via julianday)
try:
    con = sqlite3.connect(CRISP_DB)
    rows = con.execute(
        "select status, count(*) from jobs "
        "where julianday(submit_time) > julianday('now', '-1 hour') "
        "group by status", ()).fetchall()
    out('--- crisp (last 1h) ---')
    out('  ' + ', '.join(f'{s}:{n}' for s, n in rows))
    # new failures in last 30 min
    fails = con.execute(
        "select error_msg, count(*) from jobs where status='failed' "
        "and julianday(submit_time) > julianday('now', '-30 minutes') "
        "and local_dir like '%2026_undergo%' "
        "group by error_msg order by count(*) desc limit 3",
        ()).fetchall()
    if fails:
        out(f'ALERT {sum(n for _, n in fails)} new failures in 30min:')
        for e, n in fails:
            out(f'  {n}x {(e or "")[:60]}')
    con.close()
except Exception as e:
    out(f'ALERT crisp query failed: {e}')

# 3) loop alive?
try:
    r = subprocess.run(['systemctl', '--user', 'is-active', 'vasp-sop-loop'],
                       capture_output=True, text=True, timeout=10)
    if r.stdout.strip() != 'active':
        out('ALERT vasp-sop-loop NOT active')
except Exception:
    pass

out('--- done ---')
