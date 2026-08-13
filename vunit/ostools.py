# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2014-2026, Lars Asplund lars.anders.asplund@gmail.com

"""
Provides operating systems dependent functionality that can be easily
stubbed for testing
"""

from __future__ import annotations

import sys
import time
import subprocess
import threading
import shutil
from queue import Queue, Empty
from pathlib import Path
from os.path import getmtime, relpath, splitdrive
import os
from os import getcwd, makedirs
from typing import Any, Callable, Mapping, Sequence
import io

import logging

LOGGER = logging.getLogger(__name__)

# `sys.platform == "win32"` (rather than `os.name == "nt"`) so mypy narrows
# access to Windows-only stdlib attributes inside the True branch.
IS_WINDOWS_SYSTEM = sys.platform == "win32"


class ProgramStatus(object):
    """
    Maintain global program status to support graceful shutdown
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._shutting_down = False

    @property
    def is_shutting_down(self) -> bool:
        with self._lock:  # pylint: disable=not-context-manager
            return self._shutting_down

    def check_for_shutdown(self) -> None:
        if self.is_shutting_down:
            raise KeyboardInterrupt

    def shutdown(self) -> None:
        with self._lock:  # pylint: disable=not-context-manager
            LOGGER.debug("ProgramStatus.shutdown")
            self._shutting_down = True

    def reset(self) -> None:
        with self._lock:  # pylint: disable=not-context-manager
            self._shutting_down = False


PROGRAM_STATUS = ProgramStatus()


class InterruptableQueue(object):
    """
    A Queue which can be interrupted
    """

    def __init__(self) -> None:
        self._queue: Queue[str | None] = Queue()

    def get(self) -> str | None:
        """
        Get a value from the queue
        """
        while True:
            PROGRAM_STATUS.check_for_shutdown()
            try:
                return self._queue.get(timeout=0.1)
            except Empty:
                pass

    def put(self, value: str | None) -> None:
        self._queue.put(value)

    def empty(self) -> bool:
        return self._queue.empty()


class Process(object):
    """
    A simple process interface which supports asynchronously consuming the stdout and stderr
    of the process while it is running.
    """

    class NonZeroExitCode(Exception):
        pass

    def __init__(
        self,
        args: Sequence[str],
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self._args = args

        # Create process with new process group
        # Sending a signal to a process group will send it to all children
        # Hopefully this way no orphaned processes will be left behind
        if sys.platform == "win32":  # Windows
            self._process = subprocess.Popen(  # pylint: disable=consider-using-with
                args,
                bufsize=0,
                cwd=cwd,
                env=env,
                stdout=subprocess.PIPE,
                stdin=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                # Create new process group on Windows
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            )
        else:
            self._process = subprocess.Popen(  # pylint: disable=consider-using-with
                args,
                bufsize=0,
                cwd=cwd,
                env=env,
                stdout=subprocess.PIPE,
                stdin=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                # Create new process group on POSIX, setpgrp does not exist on Windows
                preexec_fn=os.setpgrp,  # pylint: disable=no-member
            )

        LOGGER.debug("Started process with pid=%i: '%s'", self._process.pid, (" ".join(args)))

        self._queue = InterruptableQueue()
        stdout = self._process.stdout
        if stdout is None:
            raise RuntimeError("Process was started without stdout PIPE")
        # ``universal_newlines=True`` above causes ``subprocess`` to return a
        # ``TextIOWrapper``. Narrow explicitly so we can access ``.buffer``.
        if not isinstance(stdout, io.TextIOWrapper):
            raise RuntimeError(f"Process stdout is not a TextIOWrapper: {type(stdout)!r}")
        self._reader = AsynchronousFileReader(stdout, self._queue)
        self._reader.start()

    def write(self, *args: Any, **kwargs: Any) -> None:
        """Write to stdin"""
        stdin = self._process.stdin
        if stdin is not None and not stdin.closed:
            stdin.write(*args, **kwargs)

    def writeline(self, line: str) -> None:
        """Write a line to stdin"""
        stdin = self._process.stdin
        if stdin is not None and not stdin.closed:
            stdin.write(line + "\n")
            stdin.flush()

    def next_line(self) -> str | int:
        """
        Return either the next line or the exit code
        """

        if not self._reader.eof():
            # Show what we received from standard output.
            msg = self._queue.get()

            if msg is not None:
                return msg

        retcode = self.wait()
        return retcode

    def wait(self) -> int:
        """
        Wait while without completely blocking to avoid
        deadlock when shutting down
        """
        while self._process.poll() is None:
            PROGRAM_STATUS.check_for_shutdown()
            time.sleep(0.05)
            LOGGER.debug("Waiting for process with pid=%i to stop", self._process.pid)
        return self._process.returncode

    def is_alive(self) -> bool:
        """
        Returns true if alive
        """
        return self._process.poll() is None

    def consume_output(self, callback: Callable[[str], Any] | None = print) -> None:
        """
        Consume the output of the process.
        The output is interpreted as UTF-8 text.

        @param callback Called for each line of output
        @raises Process.NonZeroExitCode when the process does not exit with code zero
        """

        def default_callback(*args: Any, **kwargs: Any) -> None:
            pass

        if not callback:
            callback = default_callback

        while not self._reader.eof():
            line = self._queue.get()
            if line is None:
                break

            if callback(line) is not None:
                return

        retcode = None
        while retcode is None:
            retcode = self.wait()
            if retcode != 0:
                raise Process.NonZeroExitCode

    def terminate(self) -> None:
        """
        Terminate the process
        """
        # Let's be tidy and join the threads we've started.
        if self._process.poll() is None:
            LOGGER.debug("Terminating process with pid=%i", self._process.pid)
            self._process.terminate()

        if self._process.poll() is None:
            time.sleep(0.05)

        if self._process.poll() is None:
            LOGGER.debug("Killing process with pid=%i", self._process.pid)
            self._process.kill()

        if self._process.poll() is None:
            LOGGER.debug("Waiting for process with pid=%i", self._process.pid)
            self.wait()

        LOGGER.debug(
            "Process with pid=%i terminated with code=%i",
            self._process.pid,
            self._process.returncode,
        )

        self._reader.join()
        if self._process.stdout is not None:
            self._process.stdout.close()
        if self._process.stdin is not None:
            self._process.stdin.close()

    def __del__(self) -> None:
        try:
            self.terminate()
        except KeyboardInterrupt:
            LOGGER.debug("Process.__del__: Ignoring KeyboardInterrupt")


class AsynchronousFileReader(threading.Thread):
    """
    Helper class to implement asynchronous reading of a file
    in a separate thread. Pushes read lines on a queue to
    be consumed in another thread.
    """

    def __init__(self, fd: io.TextIOWrapper, queue: InterruptableQueue, encoding: str = "utf-8") -> None:
        threading.Thread.__init__(self)

        self._fd = io.TextIOWrapper(fd.buffer, encoding=encoding, errors="ignore")
        self._queue = queue
        self._encoding = encoding

    def run(self) -> None:
        """The body of the tread: read lines and put them on the queue."""
        for line in iter(self._fd.readline, ""):
            if PROGRAM_STATUS.is_shutting_down:
                break

            string = line[:-1]

            self._queue.put(string)
        self._queue.put(None)

    def eof(self) -> bool:
        """Check whether there is no more content to expect."""
        return not self.is_alive() and self._queue.empty()


def read_file(file_name: str | os.PathLike[str], encoding: str = "utf-8", newline: str | None = None) -> str:
    """To stub during testing"""
    try:
        with io.open(file_name, "r", encoding=encoding, newline=newline) as file_to_read:
            data = file_to_read.read()
    except UnicodeDecodeError:
        LOGGER.warning(
            "Could not decode file %s using encoding %s, ignoring encoding errors",
            file_name,
            encoding,
        )
        with io.open(file_name, "r", encoding=encoding, errors="ignore", newline=newline) as file_to_read:
            data = file_to_read.read()

    return data


def write_file(file_name: str | os.PathLike[str], contents: str, encoding: str = "utf-8") -> None:
    """To stub during testing"""

    path = str(Path(file_name).parent)
    if path == "":
        path = "."

    if not file_exists(path):
        makedirs(path)

    with io.open(file_name, "wb") as file_to_write:
        file_to_write.write(contents.encode(encoding=encoding))


def file_exists(file_name: str | os.PathLike[str]) -> bool:
    """To stub during testing"""
    return Path(file_name).exists()


def get_modification_time(file_name: str | os.PathLike[str]) -> float:
    """To stub during testing"""
    return getmtime(file_name)


def get_time() -> float:
    """To stub during testing"""
    return time.time()


def renew_path(path: str | os.PathLike[str]) -> None:
    """
    Ensure path directory exists and is empty

    On Windows deleting a file will not actually delete it right away but only
    mark it for deletion. Therefore there is a race-condition between rmtree and makedirs.
    Virus scanners and file system indexers might temporarily block a file from being deleted right away

    http://stackoverflow.com/questions/27625683/can-anyone-explain-this-weird-behaviour-of-shutil-rmtree-and-shutil-copytree
    """
    if IS_WINDOWS_SYSTEM:
        retries = 10
        while retries > 0 and Path(path).exists():
            shutil.rmtree(path, ignore_errors=retries > 1)
            time.sleep(0.01)
            retries -= 1
    else:
        if Path(path).exists():
            shutil.rmtree(path)
    makedirs(path)


def simplify_path(path: str) -> str:
    """
    Return relative path towards current working directory
    unless it is a separate Windows drive
    """
    cwd = getcwd()
    drive_cwd = splitdrive(cwd)[0]
    drive_path = splitdrive(path)[0]
    if drive_path == drive_cwd:
        return relpath(path, cwd)

    return path
