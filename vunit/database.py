# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2014-2026, Lars Asplund lars.anders.asplund@gmail.com

"""
A simple file based database
"""

from __future__ import annotations

from pathlib import Path
import os
import pickle
import io
import struct
from typing import Any, BinaryIO, Iterator, Union
from vunit.ostools import renew_path

PathLike = Union[str, "os.PathLike[str]"]


class DataBase(object):
    """
    A simple file based database
    both keys and values are bytes

    The database consists of a folder with files called nodes.
    Each nodes contains four bytes denoting the key length as an
    unsigned integer followed by the key followed by the data.

    The reason to not just have the keys as the file names is that
    many operating systems does not support very long file names thus limiting the key length
    """

    def __init__(self, path: PathLike, new: bool = False) -> None:
        """
        Create database in path
        - path is a directory
        - new create new database
        """
        self._path = path

        if new:
            renew_path(path)
        elif not Path(path).exists():
            os.makedirs(path)

        # Map keys to nodes indexes
        self._keys_to_nodes: dict[bytes, int] = self._discover_nodes()
        if not self._keys_to_nodes:
            self._next_node = 0
        else:
            self._next_node = max(self._keys_to_nodes.values()) + 1

    def _discover_nodes(self) -> dict[bytes, int]:
        """
        Discover nodes already found in the database
        """
        keys_to_nodes: dict[bytes, int] = {}
        for file_base_name in os.listdir(self._path):
            key = self._read_key(str(Path(self._path) / file_base_name))
            assert key not in keys_to_nodes  # Two nodes contains the same key
            keys_to_nodes[key] = int(file_base_name)
        return keys_to_nodes

    @staticmethod
    def _read_key_from_fptr(fptr: BinaryIO) -> bytes:
        """
        Read the key from a file pointer
        first read four bytes for the key length then read the key
        """
        key_size = struct.unpack("I", fptr.read(4))[0]
        key = fptr.read(key_size)
        return key

    def _read_key(self, file_name: str) -> bytes:
        """
        Read key found in file_name
        """
        with io.open(file_name, "rb") as fptr:
            return self._read_key_from_fptr(fptr)

    def _read_data(self, file_name: str) -> bytes:
        """
        Read key found in file_name
        """
        with io.open(file_name, "rb") as fptr:
            self._read_key_from_fptr(fptr)
            data = fptr.read()
        return data

    @staticmethod
    def _write_node(file_name: str, key: bytes, value: bytes) -> None:
        """
        Write node to file
        """
        with io.open(file_name, "wb") as fptr:
            fptr.write(struct.pack("I", len(key)))
            fptr.write(key)
            fptr.write(value)

    def _to_file_name(self, key: bytes) -> str:
        """
        Convert key to file name
        """
        return str(Path(self._path) / str(self._keys_to_nodes[key]))

    def _allocate_node_for_key(self, key: bytes) -> None:
        """
        Allocate a node index for a new key
        """
        assert key not in self._keys_to_nodes
        self._keys_to_nodes[key] = self._next_node
        self._next_node += 1

    def __setitem__(self, key: bytes, value: bytes) -> None:
        if key not in self._keys_to_nodes:
            self._allocate_node_for_key(key)
        self._write_node(self._to_file_name(key), key, value)

    def __getitem__(self, key: bytes) -> bytes:
        if key not in self:
            raise KeyError(key)

        return self._read_data(self._to_file_name(key))

    def __contains__(self, key: bytes) -> bool:
        return key in self._keys_to_nodes

    def __iter__(self) -> Iterator[bytes]:
        return iter(self._keys_to_nodes.keys())


class PickledDataBase(object):
    """
    Wraps a byte based database (un)pickling the values
    Allowing storage of arbitrary Python objects
    """

    def __init__(self, database: DataBase) -> None:
        self._database = database

    def __getitem__(self, key: bytes) -> Any:
        return pickle.loads(self._database[key])

    def __setitem__(self, key: bytes, value: Any) -> None:
        self._database[key] = pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)

    def __contains__(self, key: bytes) -> bool:
        return key in self._database

    def __iter__(self) -> Iterator[bytes]:
        return iter(self._database)
