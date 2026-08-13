# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2014-2026, Lars Asplund lars.anders.asplund@gmail.com

"""
UI class PackageFacade
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from ..com import codec_generator

if TYPE_CHECKING:
    from ..design_unit import DesignUnit
    from . import VUnit
    from .source import SourceFileList


class PackageFacade(object):
    """
    User interface of a Package
    """

    def __init__(
        self,
        parent: VUnit,
        library_name: str,
        package_name: str,
        design_unit: DesignUnit,
    ) -> None:
        self._parent = parent
        self._library_name = library_name
        self._package_name = package_name
        self._design_unit = design_unit

    def generate_codecs(
        self,
        codec_package_name: str | None = None,
        used_packages: list[str] | None = None,
        output_file_name: str | Path | None = None,
    ) -> SourceFileList:
        """
        Generates codecs for the datatypes in this Package
        """
        if codec_package_name is None:
            codec_package_name = self._package_name + "_codecs"

        if output_file_name is None:
            codecs_path = Path(self._parent.codecs_path) / self._library_name
            file_extension = Path(self._design_unit.source_file.name).suffix
            output_file_name = codecs_path / (codec_package_name + file_extension)

        codec_generator.generate_codecs(self._design_unit, codec_package_name, used_packages, output_file_name)

        return self._parent.add_source_files(output_file_name, self._library_name)
