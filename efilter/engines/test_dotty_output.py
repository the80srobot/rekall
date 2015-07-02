import unittest

from efilter import query


class RuleAnalyzerTest(unittest.TestCase):
    def assertOutput(self, original, output):
        q = query.Query(original, syntax="dotty")
        actual_output = q.run_engine("dotty_output")
        self.assertEqual(output, actual_output)

    def testBasic(self):
        self.assertOutput(original="5 + 5 == 10",
                          output="5 + 5 == 10")
