# EFILTER Forensic Query Language
#
# Copyright 2015 Google Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
EFILTER dotty syntax output.
"""

__author__ = "Adam Sindelar <adamsh@google.com>"

from efilter import engine
from efilter import expression


class DottyOutput(engine.VisitorEngine):
    """Produces equivalent Dotty output to the AST."""

    def visit_Expression(self, expr, **_):
        pass


engine.Engine.register_engine(DottyOutput, "dotty_output")
