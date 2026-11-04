import unittest
from backend.score_alignment import reconcile_note_timing


class NoteAlignmentTests(unittest.TestCase):
    def test_matched_notes_stay_unchanged(self):
        words=['我','爱','你'];spans=[.4,.4,.4]
        result=reconcile_note_timing(words,spans,words,spans,[60,61,62],[2,2,2])
        self.assertEqual(result[0],words);self.assertFalse(result[4])

    def test_lost_attack_is_recovered_at_word_boundary(self):
        result=reconcile_note_timing(['我','爱','你'],[.4,.4,.4],['我','爱'],[.4,.8],[60,62],[2,2])
        self.assertEqual(result[0],['我','爱','你']);self.assertEqual(result[3],[2,2,2]);self.assertTrue(result[4])
        for actual in result[1]: self.assertAlmostEqual(actual,.4)

    def test_no_merge_between_repeated_word_attacks(self):
        result=reconcile_note_timing(['你','你'],[.5,.5],['你'],[1.],[60],[2])
        self.assertEqual(result[0],['你','你']);self.assertEqual(result[3],[2,2])

    def test_wrong_slur_word_is_repaired_even_when_attacks_match(self):
        result=reconcile_note_timing(['我','爱'],[.5,.5],['我','错','爱'],[.25,.25,.5],[60,60,62],[2,3,2])
        self.assertEqual(result[0],['我','爱']);self.assertEqual(result[3],[2,2]);self.assertTrue(result[4])

    def test_too_short_unit_is_rejected(self):
        with self.assertRaises(ValueError):
            reconcile_note_timing(['我','爱'],[.01,.99],['我'],[1.],[60],[2])

    def test_bad_total_is_rejected(self):
        with self.assertRaises(ValueError):
            reconcile_note_timing(['我'],[1.],['我'],[2.],[60],[2])


if __name__=='__main__': unittest.main()
