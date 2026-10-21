"""Run allowlisted job handlers with polling and durable retry state."""
import argparse
import hashlib
import json
import signal
import threading
from queue_store import Queue


def execute(job):
    if job['kind'] == 'echo':
        return job['payload']
    if job['kind'] == 'sha256':
        value = json.dumps(job['payload'], sort_keys=True, separators=(',', ':'), allow_nan=False)
        return {'sha256': hashlib.sha256(value.encode()).hexdigest()}
    raise RuntimeError('Intentional failure for retry/dead-letter demonstration')


def run_once(queue):
    job = queue.claim()
    if job is None:
        return False
    try:
        result = execute(job)
    except Exception as error:
        queue.finish(job['id'], job['lease_token'], error=str(error))
    else:
        queue.finish(job['id'], job['lease_token'], result=result)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', default='relayqueue.db')
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    queue = Queue(args.db)
    if args.once:
        run_once(queue)
        return
    stopped = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stopped.set())
    while not stopped.is_set():
        if not run_once(queue):
            stopped.wait(.25)


if __name__ == '__main__':
    main()
