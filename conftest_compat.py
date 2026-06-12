"""Python 3.14 compatibility shim.

Python 3.14 removed ``ast.Str``, ``ast.Num``, ``ast.Bytes``, ``ast.NameConstant``
and ``ast.Ellipsis``.  Older Werkzeug (2.x) still references ``ast.Str`` when
compiling URL rules.  This conftest adds the aliases back so the test suite
can run on Python 3.14.
"""

import ast
import sys

if sys.version_info >= (3, 14):
    for _name in ("Str", "Num", "Bytes", "NameConstant", "Ellipsis"):
        if not hasattr(ast, _name):
            setattr(ast, _name, ast.Constant)
