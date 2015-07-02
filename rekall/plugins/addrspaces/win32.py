# Rekall Memory Forensics
# Copyright (C) 2012 Michael Cohen
# Copyright 2013 Google Inc. All Rights Reserved.
#
# Authors:
# Michael Cohen <scudette@gmail.com>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or (at
# your option) any later version.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA 02111-1307 USA
#
"""This is a windows specific address space."""
import pywintypes
import struct
import weakref
import win32file

from rekall import addrspace
from rekall.plugins.addrspaces import standard


def CTL_CODE(DeviceType, Function, Method, Access):
    return (DeviceType<<16) | (Access << 14) | (Function << 2) | Method


# IOCTLS for interacting with the driver.
INFO_IOCTRL = CTL_CODE(0x22, 0x103, 0, 3)

PAGE_SHIFT = 12


class Win32AddressSpace(addrspace.CachingAddressSpaceMixIn,
                        addrspace.RunBasedAddressSpace):
    """ This is a direct file AS for use in windows.

    In windows, in order to open raw devices we need to use the win32 apis. This
    address space allows us to open the raw device as exported by e.g. the
    winpmem driver.
    """

    CHUNK_SIZE = 0x1000

    def _OpenFileForRead(self, path):
        try:
            fhandle = self.fhandle = win32file.CreateFile(
                path,
                win32file.GENERIC_READ,
                win32file.FILE_SHARE_READ | win32file.FILE_SHARE_WRITE,
                None,
                win32file.OPEN_EXISTING,
                win32file.FILE_ATTRIBUTE_NORMAL,
                None)

            self._closer = weakref.ref(
                self, lambda x: win32file.CloseHandle(fhandle))

            self.write_enabled = False

        except pywintypes.error as e:
            raise IOError("Unable to open %s: %s" % (path, e))

    def _read_chunk(self, addr, length):
        offset, available_length = self._get_available_buffer(addr, length)

        # Offset is pointing into invalid range, pad until the next range.
        if offset is None:
            return "\x00" * min(length, available_length)

        win32file.SetFilePointer(self.fhandle, offset, 0)
        _, data = win32file.ReadFile(
            self.fhandle, min(length, available_length))

        return data

    def write(self, addr, data):
        length = len(data)
        offset, available_length = self._get_available_buffer(addr, length)
        if offset is None:
            # Do not allow writing to reserved areas.
            return

        to_write = min(len(data), available_length)
        win32file.SetFilePointer(self.fhandle, offset, 0)

        win32file.WriteFile(self.fhandle, data[:to_write])

        return to_write

    def close(self):
        win32file.CloseHandle(self.fhandle)


class Win32FileAddressSpace(Win32AddressSpace):
    __name = "win32file"

    ## We should be the AS of last resort but in front of the non win32 version.
    order = standard.FileAddressSpace.order - 5
    __image = True

    def __init__(self, base=None, filename=None, **kwargs):
        self.as_assert(base == None, 'Must be first Address Space')
        super(Win32FileAddressSpace, self).__init__(**kwargs)
        self.phys_base = self

        path = filename or self.session.GetParameter("filename")

        self.as_assert(path, "Filename must be specified in session (e.g. "
                       "session.SetParameter('filename', 'MyFile.raw').")

        self.fname = path

        # The file is just a regular file, we open for reading.
        self._OpenFileForRead(path)

        # If we can not get the file size it means this is not a regular file -
        # maybe a device.
        try:
            self.runs.insert((0, 0, win32file.GetFileSize(self.fhandle)))
        except pywintypes.error:
            raise addrspace.ASAssertionError("Not a regular file.")

    
class WinPmemAddressSpace(Win32AddressSpace):
    """An address space specifically designed for communicating with WinPmem."""

    __name = "winpmem"
    __image = True

    # This is a live address space.
    volatile = True

    # We must be in front of the regular file based AS.
    order = Win32FileAddressSpace.order - 5

    def __init__(self, base=None, filename=None, session=None, **kwargs):
        self.as_assert(base == None, 'Must be first Address Space')
        path = filename or session.GetParameter("filename")
        self.as_assert(path.startswith("\\\\"),
                       "Filename does not look like a device.")

        super(WinPmemAddressSpace, self).__init__(
            filename=filename, session=session, **kwargs)

        try:
            # First open for write in case the driver is in write mode.
            self._OpenFileForWrite(path)
        except IOError:
            self._OpenFileForRead(path)

        try:
            self.ParseMemoryRuns()
        except Exception:
            self.runs.insert((0, 0, 2**63))

    def _OpenFileForWrite(self, path):
        try:
            fhandle = self.fhandle = win32file.CreateFile(
                path,
                win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                win32file.FILE_SHARE_READ | win32file.FILE_SHARE_WRITE,
                None,
                win32file.OPEN_EXISTING,
                win32file.FILE_ATTRIBUTE_NORMAL,
                None)
            self.write_enabled = True
            self._closer = weakref.ref(
                self, lambda x: win32file.CloseHandle(fhandle))

        except pywintypes.error as e:
            raise IOError("Unable to open %s: %s" % (path, e))

    FIELDS = (["CR3", "NtBuildNumber", "KernBase", "KDBG"] +
              ["KPCR%02d" % i for i in xrange(32)] +
              ["PfnDataBase", "PsLoadedModuleList", "PsActiveProcessHead"] +
              ["Padding%s" % i for i in xrange(0xff)] +
              ["NumberOfRuns"])

    def ParseMemoryRuns(self):
        result = win32file.DeviceIoControl(
            self.fhandle, INFO_IOCTRL, "", 102400, None)

        fmt_string = "Q" * len(self.FIELDS)
        self.memory_parameters = dict(zip(self.FIELDS, struct.unpack_from(
            fmt_string, result)))

        self.dtb = self.memory_parameters["CR3"]
        self.session.SetCache("dtb", int(self.dtb))

        offset = struct.calcsize(fmt_string)

        for x in xrange(self.memory_parameters["NumberOfRuns"]):
            start, length = struct.unpack_from("QQ", result, x * 16 + offset)
            self.runs.insert((start, start, length))

        # Get the kernel base directly from the winpmem driver if that is
        # available.
        kernel_base = self.memory_parameters["KernBase"]
        if kernel_base > 0:
            self.session.SetCache("kernel_base", kernel_base)
