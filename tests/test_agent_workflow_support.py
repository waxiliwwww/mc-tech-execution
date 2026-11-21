"""Real durable job records: bounded waiting never changes solver lifecycle."""
import asyncio
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import business
import openmc_jobs as jobs


class JobWaiting(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.workspace=Path(self.temp.name)
        self.addCleanup(patch.stopall);patch.object(business,'WORKSPACE',self.workspace).start()
        self.identifier='a'*32;self.folder=self.workspace/'.mc-jobs'/self.identifier
        self.folder.mkdir(parents=True)
        self.record={'job_id':self.identifier,'state':'queued','mode':'run','submitted_at':time.time()}
        jobs.atomic_json(self.folder/'job.json',self.record)

    def test_default_still_returns_immediately_and_timeout_does_not_cancel(self):
        before=(self.folder/'job.json').read_bytes()
        self.assertNotIn('wait',jobs.status(self.identifier))
        start=time.monotonic();result=jobs.status(self.identifier,wait_seconds=1)
        self.assertGreaterEqual(time.monotonic()-start,.9)
        self.assertLess(time.monotonic()-start,2)
        self.assertEqual(result['state'],'queued');self.assertTrue(result['wait']['timed_out'])
        self.assertEqual((self.folder/'job.json').read_bytes(),before)
        self.assertEqual(len(list((self.workspace/'.mc-jobs').iterdir())),1)

    def test_wait_returns_actual_terminal_record_early(self):
        def finish():
            time.sleep(.15)
            jobs.atomic_json(self.folder/'job.json',dict(self.record,state='failed',exit_code=2,error='controlled record transition'))
        thread=threading.Thread(target=finish);thread.start();self.addCleanup(thread.join)
        start=time.monotonic();result=jobs.status(self.identifier,wait_seconds=3)
        self.assertEqual(result['state'],'failed');self.assertEqual(result['exit_code'],2)
        self.assertFalse(result['wait']['timed_out']);self.assertLess(time.monotonic()-start,1.5)

    def test_invalid_wait_rejects_before_changing_record(self):
        before=(self.folder/'job.json').read_bytes()
        for value in [-1,31,True,1.5]:
            with self.subTest(value=value), self.assertRaises(ValueError):jobs.status(self.identifier,value)
        self.assertEqual((self.folder/'job.json').read_bytes(),before)
