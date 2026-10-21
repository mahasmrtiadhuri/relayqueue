from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest
from queue_store import Conflict, Queue
from worker import run_once

class QueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.now = 1000.0
        self.path = Path(self.temp.name) / 'queue.db'
        self.q = Queue(self.path, clock=lambda: self.now)
    def tearDown(self):
        self.temp.cleanup()
    def test_idempotency_replay_and_conflict(self):
        first, created = self.q.submit('echo', {'b': 2, 'a': 1}, 'key')
        second, replay = self.q.submit('echo', {'a': 1, 'b': 2}, 'key')
        self.assertTrue(created)
        self.assertFalse(replay)
        self.assertEqual(first['id'], second['id'])
        with self.assertRaises(Conflict): self.q.submit('echo', {'a': 3}, 'key')
    def test_concurrent_submit_deduplicates(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.q.submit('echo', {}, 'same'), range(24)))
        self.assertEqual(sum(created for _, created in results), 1)
        self.assertEqual(len({job['id'] for job, _ in results}), 1)
    def test_concurrent_workers_have_unique_claims(self):
        ids = {self.q.submit('echo', {'n': i})[0]['id'] for i in range(30)}
        with ThreadPoolExecutor(max_workers=10) as pool:
            jobs = list(pool.map(lambda _: self.q.claim(), range(50)))
        claims = [job['id'] for job in jobs if job]
        self.assertEqual(set(claims), ids)
        self.assertEqual(len(claims), 30)
    def test_restart_retains_work(self):
        job, _ = self.q.submit('echo', {'hello': 'world'})
        restarted = Queue(self.path, clock=lambda: self.now)
        run_once(restarted)
        self.assertEqual(restarted.get(job['id'])['result'], {'hello': 'world'})
        self.assertNotIn('lease_token', restarted.get(job['id']))
    def test_crash_recovery_rejects_stale_ack(self):
        self.q.submit('echo', {})
        old = self.q.claim(5)
        self.now += 6
        new = self.q.claim()
        self.assertEqual(old['id'], new['id'])
        self.assertNotEqual(old['lease_token'], new['lease_token'])
        with self.assertRaises(Conflict): self.q.finish(old['id'], old['lease_token'])
        self.q.finish(new['id'], new['lease_token'], result={'ok': True})
    def test_expired_ack_rejected_before_reclaim(self):
        self.q.submit('echo', {})
        job = self.q.claim(1)
        self.now += 1
        with self.assertRaises(Conflict): self.q.finish(job['id'], job['lease_token'])
    def test_backoff_dead_letter_and_manual_retry(self):
        job, _ = self.q.submit('fail', {}, max_attempts=2)
        run_once(self.q)
        self.assertIsNone(self.q.claim())
        self.now += 1
        run_once(self.q)
        self.assertEqual(self.q.get(job['id'])['state'], 'dead')
        self.q.retry(job['id'])
        self.assertEqual(self.q.get(job['id'])['attempts'], 0)
        self.assertEqual([e['event'] for e in self.q.events(job['id'])], ['submitted','claimed','retry_scheduled','claimed','dead','manual_retry'])
    def test_final_attempt_crash_goes_dead(self):
        job, _ = self.q.submit('echo', {}, max_attempts=1)
        self.q.claim(1)
        self.now += 2
        self.assertIsNone(self.q.claim())
        self.assertEqual(self.q.get(job['id'])['state'], 'dead')
    def test_validation(self):
        for kind, payload, attempts in [('shell',{},3),('echo',[],3),('echo',{},True),('echo',{},0)]:
            with self.assertRaises(ValueError): self.q.submit(kind, payload, max_attempts=attempts)
    def test_metrics(self):
        self.q.submit('sha256', {'x': 1})
        run_once(self.q)
        self.assertEqual(self.q.metrics(), {'queued':0,'running':0,'succeeded':1,'dead':0})
