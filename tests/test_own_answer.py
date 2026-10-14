import unittest

import aipet
import aipetlib.ui.pet_style as pet_style


class Var:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class OwnAnswerTests(unittest.TestCase):
    def card(self, *rows):
        card = object.__new__(aipet.Detail)
        card._qvars = list(rows)
        return card

    def test_typed_text_is_only_sent_while_its_radio_button_is_picked(self):
        q = {"question": "Push now?"}
        choice, own = Var("No, push now"), Var("what was fixed first")
        card = self.card((q, False, choice, {}, own, Var(False)))
        self.assertEqual(card.question_answers(), {"Push now?": "No, push now"})  # typed, but an option is picked
        choice.set(pet_style.OWN_CHOICE)
        self.assertEqual(card.question_answers(), {"Push now?": "what was fixed first"})
        own.set("  ")
        self.assertIsNone(card.question_answers())  # own answer picked but empty: nothing to send

    def test_multi_select_adds_own_answer_only_when_ticked(self):
        q = {"question": "Which?"}
        own_on = Var(False)
        card = self.card((q, True, Var(pet_style.NO_CHOICE), {"A": Var(True), "B": Var(False)}, Var("C"), own_on))
        self.assertEqual(card.question_answers(), {"Which?": ["A"]})
        own_on.set(True)
        self.assertEqual(card.question_answers(), {"Which?": ["A", "C"]})


if __name__ == "__main__":
    unittest.main()
