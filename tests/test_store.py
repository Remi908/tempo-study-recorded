import concurrent.futures
import csv
import io
from pathlib import Path
import sys
import tempfile
import unittest
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from store import Store, StudyError, Finished, TEMPOS, within_tolerance

class StudyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/'study.sqlite3'
        self.store=Store(self.path)
        self.token,self.key=self.store.start('Remi')

    def tearDown(self):
        self.temp.cleanup()

    def trial(self,key=None):
        return self.store.reserve(key or self.key,str(uuid.uuid4()))[0]

    def serve_and_rate(self,trial):
        self.assertTrue(self.store.accept(trial['id'],trial['target_bpm']+.2,trial['id'],'test.wav','generated'))
        self.store.rate(trial['username_key'],trial['id'],4)

    def test_exactly_six_distinct_tempos_and_then_complete(self):
        assigned=[]
        for _ in range(len(TEMPOS)):
            trial=self.trial();assigned.append(trial['target_bpm']);self.serve_and_rate(trial)
        self.assertEqual(sorted(assigned),list(TEMPOS))
        with self.assertRaises(Finished):self.trial()
        self.assertTrue(self.store.progress(self.key)['complete'])

    def test_new_device_case_changes_and_restart_preserve_history(self):
        first=self.trial();self.serve_and_rate(first)
        other=Store(self.path);token,key=other.start('  rEmI  ')
        self.assertEqual(key,self.key)
        remaining=[]
        for _ in range(len(TEMPOS)-1):
            t=other.reserve(key,str(uuid.uuid4()))[0];remaining.append(t['target_bpm']);self.serve_and_rate(t)
        self.assertNotIn(first['target_bpm'],remaining)
        self.assertEqual(other.progress(key)['rated'],len(TEMPOS))

    def test_simultaneous_tabs_create_one_trial_only(self):
        def request(_):return self.store.reserve(self.key,str(uuid.uuid4()))
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
            results=list(pool.map(request,range(10)))
        self.assertEqual(sum(created for _,created in results),1)
        self.assertEqual(len({t['id'] for t,_ in results}),1)
        self.assertEqual(len({t['target_bpm'] for t,_ in results}),1)

    def test_transport_retry_keeps_assigned_tempo_and_seed(self):
        t=self.trial();again,created=self.store.reserve(self.key,t['id'])
        self.assertFalse(created);self.assertEqual(again,t)
        self.serve_and_rate(t)
        rated,created=self.store.reserve(self.key,t['id'])
        self.assertFalse(created);self.assertEqual(rated['target_bpm'],t['target_bpm'])

    def test_failed_sixth_attempt_ends_session_with_five_responses(self):
        for _ in range(len(TEMPOS)-1):self.serve_and_rate(self.trial())
        failed=self.trial();self.store.fail(failed['id'],'No matching fallback')
        with self.assertRaises(Finished):self.trial()
        progress=self.store.progress(self.key)
        self.assertEqual(progress['delivered'],len(TEMPOS)-1)
        self.assertEqual(progress['attempted'],len(TEMPOS))
        self.assertEqual(progress['remaining'],0)
        self.assertTrue(progress['complete'])

    def test_all_failed_attempts_are_distinct_and_stop_after_six(self):
        targets=[]
        for _ in TEMPOS:
            trial=self.trial();targets.append(trial['target_bpm'])
            self.store.fail(trial['id'],'No audio')
        self.assertEqual(sorted(targets),list(TEMPOS))
        with self.assertRaises(Finished):self.trial()
        self.assertEqual(self.store.progress(self.key)['rated'],0)
        self.assertTrue(self.store.progress(self.key)['complete'])
        self.assertEqual(len(list(csv.reader(io.StringIO(self.store.export_csv())))),1)

    def test_legacy_failed_attempts_still_count_when_slot_was_deleted(self):
        failed=self.trial();self.store.fail(failed['id'],'Old failure')
        with self.store.connect() as c:
            c.execute('DELETE FROM tempo_slots WHERE trial_id=?',(failed['id'],))
        other=Store(self.path);_,key=other.start('REMI')
        for _ in range(len(TEMPOS)-1):
            t=other.reserve(key,str(uuid.uuid4()))[0]
            self.assertNotEqual(t['target_bpm'],failed['target_bpm'])
            other.fail(t['id'],'No audio')
        with self.assertRaises(Finished):other.reserve(key,str(uuid.uuid4()))
        self.assertEqual(other.progress(key)['attempted'],len(TEMPOS))

    def test_last_pending_or_ready_trial_can_still_be_listened_to_and_rated(self):
        for _ in range(len(TEMPOS)-1):
            trial=self.trial();self.store.fail(trial['id'],'No audio')
        last=self.trial()
        self.assertFalse(self.store.progress(self.key)['complete'])
        self.assertEqual(self.store.progress(self.key)['remaining'],0)
        self.store.accept(last['id'],last['target_bpm'],'last','last.wav','folder')
        self.assertFalse(self.store.progress(self.key)['complete'])
        self.store.rate(self.key,last['id'],5)
        self.assertTrue(self.store.progress(self.key)['complete'])
        self.assertEqual(self.store.progress(self.key)['rated'],1)

    def test_concurrent_requests_after_failure_only_create_one_new_tempo(self):
        failed=self.trial();self.store.fail(failed['id'],'No audio')
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
            results=list(pool.map(lambda _: self.store.reserve(self.key,str(uuid.uuid4())),range(10)))
        self.assertEqual(sum(created for _,created in results),1)
        self.assertEqual(len({t['id'] for t,_ in results}),1)
        self.assertTrue(all(t['target_bpm']!=failed['target_bpm'] for t,_ in results))

    def test_retrying_failed_request_id_does_not_draw_another_tempo(self):
        trial=self.trial();self.store.fail(trial['id'],'No audio')
        repeated,created=self.store.reserve(self.key,trial['id'])
        self.assertFalse(created)
        self.assertEqual(repeated['status'],'failed')
        self.assertEqual(self.store.progress(self.key)['attempted'],1)

    def test_ready_clip_never_releases_its_tempo(self):
        t=self.trial();self.store.accept(t['id'],t['target_bpm'],'hash','test.wav','folder')
        self.store.fail(t['id'],'Late network failure')
        self.assertEqual(self.store.get(t['id'])['status'],'ready')
        new,created=self.store.reserve(self.key,str(uuid.uuid4()))
        self.assertFalse(created);self.assertEqual(new['id'],t['id'])

    def test_restart_preserves_pending_and_served_tempos(self):
        t=self.trial();self.serve_and_rate(t);pending=self.trial()
        self.store.recover()
        self.assertEqual(self.store.get(pending['id'])['status'],'failed')
        with self.store.connect() as c:
            slots=list(c.execute('SELECT target_bpm FROM tempo_slots'))
        self.assertEqual({r[0] for r in slots},{t['target_bpm'],pending['target_bpm']})
        for _ in range(len(TEMPOS)-2):
            trial=self.trial()
            self.assertNotIn(trial['target_bpm'],(t['target_bpm'],pending['target_bpm']))
            self.serve_and_rate(trial)
        with self.assertRaises(Finished):self.trial()

    def test_gate_is_inclusive_and_does_not_half_double_correct(self):
        for n in (70,75,80):self.assertTrue(within_tolerance(n,75))
        for n in (69.99,80.01,None,float('nan'),float('inf'),150,37.5,'75',True):
            self.assertFalse(within_tolerance(n,75))
        t=self.trial()
        self.assertFalse(self.store.accept(t['id'],t['target_bpm']+5.01,'bad','bad.wav','generated'))
        self.assertTrue(self.store.accept(t['id'],t['target_bpm']-5,'good','good.wav','folder'))

    def test_waveform_fingerprint_also_cannot_repeat(self):
        t=self.trial();self.store.accept(t['id'],t['target_bpm'],'same-hash','a.wav','generated');self.store.rate(self.key,t['id'],3)
        second=self.trial()
        self.assertFalse(self.store.accept(second['id'],second['target_bpm'],'same-hash','renamed.wav','folder'))

    def test_ratings_are_idempotent_and_use_actual_served_tempo(self):
        t=self.trial();measured=t['target_bpm']-1.25
        self.store.accept(t['id'],measured,'hash','a.wav','folder')
        first=self.store.rate(self.key,t['id'],5);second=self.store.rate(self.key,t['id'],1)
        self.assertEqual(first,second)
        self.assertEqual(first,{'username':'Remi','rating':5,'tempo':measured})
        rows=list(csv.reader(io.StringIO(self.store.export_csv())))
        self.assertEqual(rows[0],['username','rating','tempo']);self.assertEqual(len(rows),2)

    def test_two_usernames_have_independent_six_tempo_sets(self):
        _,second=self.store.start('someone_else')
        for _ in range(len(TEMPOS)):
            self.serve_and_rate(self.trial());self.serve_and_rate(self.trial(second))
        self.assertTrue(self.store.progress(second)['complete'])

    def test_unknown_tokens_cross_user_reads_and_invalid_ratings_rejected(self):
        t=self.trial();_,other=self.store.start('other_user')
        with self.assertRaises(StudyError):self.store.identify('wrong-token')
        with self.assertRaises(StudyError):self.store.get(t['id'],other)
        with self.assertRaises(StudyError):self.store.reserve(other,t['id'])
        with self.assertRaises(StudyError):self.store.rate(self.key,t['id'],5)
        self.store.accept(t['id'],t['target_bpm'],'hash','a.wav','generated')
        for rating in (0,6,True,3.5):
            with self.assertRaises(StudyError):self.store.rate(self.key,t['id'],rating)

if __name__=='__main__':unittest.main()
