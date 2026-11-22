import unittest

from _support import set_config
from transcription import is_hallucination, post_process_transcription

PROMPT = "Hello, welcome to my lecture. How are you today? We will discuss important topics, commas, and more."


class HallucinationGuardTests(unittest.TestCase):
    def setUp(self):
        set_config(PROMPT, 'model_options', 'common', 'initial_prompt')
        set_config(False, 'post_processing', 'remove_trailing_period')
        set_config(True, 'post_processing', 'add_trailing_space')
        set_config(False, 'post_processing', 'remove_capitalization')

    def test_the_2026_11_09_loop_is_dropped(self):
        # The real history.log entry: one transcript of "Thank you." thousands of times.
        self.assertTrue(is_hallucination('Thank you. ' * 3000))
        self.assertEqual(post_process_transcription('Thank you. ' * 3000), '')

    def test_any_short_phrase_loop_is_dropped(self):
        self.assertTrue(is_hallucination('I am here. I am here. I am here.'))
        self.assertTrue(is_hallucination('Okay okay okay okay'))

    def test_lone_silence_phrases_are_dropped(self):
        for text in ['Thank you.', 'thank you', 'Thanks for watching!', ' Thank you very much. ', 'You']:
            self.assertTrue(is_hallucination(text), text)

    def test_initial_prompt_sentences_are_dropped(self):
        self.assertTrue(is_hallucination('Hello, welcome to my lecture.', PROMPT))
        self.assertTrue(is_hallucination('How are you today?', PROMPT))
        self.assertFalse(is_hallucination('How are you today?', None))

    def test_real_speech_is_kept(self):
        for text in [
            'Email Eric from my Gmail with the two website addresses.',
            'Thank you, that fixed it.',
            'Perfect.',
            'Thank you. Now open the other file.',
            'Yes yes, run it again.',
        ]:
            self.assertFalse(is_hallucination(text, PROMPT), text)
        self.assertEqual(post_process_transcription('Thank you, that fixed it.'), 'Thank you, that fixed it. ')

    def test_empty_is_not_flagged(self):
        self.assertFalse(is_hallucination(''))
        self.assertFalse(is_hallucination('   ...  '))


if __name__ == '__main__':
    unittest.main()
