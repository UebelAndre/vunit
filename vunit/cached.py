# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2014-2026, Lars Asplund lars.anders.asplund@gmail.com

"""
Utility to perform costly operation on file contents which can be cached
"""

from __future__ import annotations

import os
from typing import Callable, TypeVar
from vunit.database import PickledDataBase
from vunit.hashing import hash_string
from vunit.ostools import read_file

T = TypeVar("T")


def cached(
    key: str,
    function: Callable[[str], T],
    file_name: str,
    encoding: str,
    *,
    database: PickledDataBase | None = None,
    newline: str | None = None,
) -> T:
    """
    Call function with file content if an update is needed
    """

    if database is None:
        # Without a database just return the function of the contents
        return function(read_file(file_name, encoding=encoding, newline=newline))

    function_key = f"{key!s}({file_name!s}, newline={newline!s})".encode()
    content, content_hash = _file_content_hash(file_name, encoding, database, newline=newline)

    if function_key not in database:
        # We do not have a cached version of this computation
        # recompute and update database
        if content is None:
            content = read_file(file_name, encoding=encoding, newline=newline)
        result = function(content)
        database[function_key] = content_hash, result
        return result

    old_content_hash, old_result = database[function_key]
    if old_content_hash == content_hash:
        # ``old_result`` was pickled by an earlier ``cached`` call for this
        # ``function`` so it is by construction a ``T``. Assigning through
        # a typed local avoids the ``no-any-return`` mypy error that would
        # otherwise arise from the ``Any`` returned by the pickled database.
        cached_result: T = old_result
        return cached_result

    # Content hash differs, recompute and update database
    if content is None:
        content = read_file(file_name, encoding=encoding, newline=newline)
    result = function(content)
    database[function_key] = content_hash, result
    return result


def file_content_hash(
    file_name: str,
    encoding: str,
    database: PickledDataBase | None = None,
) -> str:
    """
    Returns the hash of the contents of the file

    Use the database to keep a persistent cache of the last content
    hash.
    """
    _, content_hash = _file_content_hash(file_name, encoding, database)
    return content_hash


def _file_content_hash(
    file_name: str,
    encoding: str,
    database: PickledDataBase | None = None,
    newline: str | None = None,
) -> tuple[str | None, str]:
    """
    Returns the file content as well as the hash of the content

    Use the database to keep a persistent cache of the last content
    hash.  If the file modification date has not changed assume the
    hash is the same and do not re-open the file.
    """

    if database is None:
        content = read_file(file_name, encoding=encoding, newline=newline)
        return content, hash_string(content)

    key = f"cached._file_content_hash({file_name!s}, newline={newline!s})".encode()

    if key not in database:
        content = read_file(file_name, encoding=encoding, newline=newline)
        content_hash = hash_string(content)
        timestamp = os.path.getmtime(file_name)
        database[key] = timestamp, content_hash
        return content, content_hash

    timestamp = os.path.getmtime(file_name)
    last_timestamp, last_content_hash = database[key]
    if timestamp != last_timestamp:
        content = read_file(file_name, encoding=encoding, newline=newline)
        content_hash = hash_string(content)
        database[key] = timestamp, content_hash
        return content, content_hash

    return None, last_content_hash
