"""Request-intent classification (multilingual, refusals win)."""
import unittest

import _paths
_paths.ensure()
from ghost_audit.intent import classify

DELETION = [
    "Please delete my account and all personal data you hold about me.",
    "I want my profile erased. Right to be forgotten.",
    "Solicito la eliminación de mi cuenta.",
    "Je demande la suppression de mes données.",
    "Bitte löschen Sie mein Konto.",
    "Gostaria de excluir minha conta.",
    "アカウントを削除してください。",
    "계정을 삭제해 주세요.",
    "请删除我的账户。",
    "Please close my account, I no longer use it, and delete my data.",
]
NOT_DELETION = [
    "Could you send me a copy of my data? Please don't delete anything.",
    "Please unsubscribe me from marketing newsletters.",
    "My surname is misspelled, please correct it.",
    "What personal information do you hold about me?",
    "I can't access my profile, please help with resetting the login.",
    "Remove me from marketing lists only; keep my account active.",
    "This is not a request to delete my account, I just want my invoices.",
    "Hello, how are you?",
]


class Intent(unittest.TestCase):
    def test_deletion_requests(self):
        for text in DELETION:
            ok, conf, why = classify(text)
            self.assertTrue(ok, f"{text!r}: {why}")
            self.assertGreaterEqual(conf, 0.7)

    def test_non_deletion_requests(self):
        for text in NOT_DELETION:
            ok, _conf, why = classify(text)
            self.assertFalse(ok, f"{text!r}: {why}")

    def test_refusal_beats_deletion_verb(self):
        ok, _, why = classify("I am not asking for deletion, please delete nothing, keep my account active.")
        self.assertFalse(ok)
        self.assertTrue(why.startswith("explicit non-deletion"))

    def test_curly_apostrophe(self):
        self.assertFalse(classify("Please don’t delete my account, just export it.")[0])


if __name__ == "__main__":
    unittest.main()
