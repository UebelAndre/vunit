# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2014-2026, Lars Asplund lars.anders.asplund@gmail.com

"""
Handles Cadence Incisive .cds files
"""

from __future__ import annotations

import os
import re
from ..ostools import read_file, write_file


class CDSFile(dict[str, str]):
    """
    Handles Cadence Incisive .cds files

    Only cares about 'define' but other lines are kept intact
    """

    _re_define = re.compile(r'\s*define\s+([a-zA-Z0-9_]+)\s+"?(.*?)"?(#|$)', re.IGNORECASE)

    @classmethod
    def parse(cls, file_name: str | os.PathLike[str]) -> "CDSFile":
        """
        Parse file_name and create CDSFile instance
        """
        contents = read_file(file_name)

        other_lines: list[str] = []
        defines: dict[str, str] = {}
        for line in contents.splitlines():
            match = cls._re_define.match(line)

            if match is None:
                other_lines.append(line)
            else:
                defines[match.group(1)] = match.group(2)
        return cls(defines, other_lines)

    def __init__(
        self,
        defines: dict[str, str] | None = None,
        other_lines: list[str] | None = None,
    ) -> None:
        defines = {} if defines is None else defines
        other_lines = [] if other_lines is None else other_lines
        dict.__init__(self, defines)
        self._other_lines = other_lines

    def write(self, file_name: str | os.PathLike[str]) -> None:
        """
        Write cds file to file named 'file_name'
        """
        contents = (
            "\n".join(self._other_lines + [f'define {item[0]!s} "{item[1]!s}"' for item in sorted(self.items())]) + "\n"
        )
        write_file(file_name, contents)
