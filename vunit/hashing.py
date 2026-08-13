# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2014-2026, Lars Asplund lars.anders.asplund@gmail.com

"""
Wrapper arround selected hash method
"""

from __future__ import annotations

import hashlib


def hash_string(string: str) -> str:
    """
    returns hash of bytes
    """
    return hashlib.sha1(string.encode(encoding="utf-8")).hexdigest()
