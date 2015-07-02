# Rekall Memory Forensics
# Copyright 2014 Google Inc. All Rights Reserved.
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
""" This file adds pagefile support.

Although much of the address translation machinery occurs in hardware, when a
page fault occurs the operating system's pager is called. The pager is
responsible for faulting in invalid pages, and hence we need operating system
specific support.

Rekall's base paging address spaces emulate the hardware's MMU page translation,
but when the page is invalid Rekall emulates the operating system's page fault
handling code. The correct (OS depended) address space is selected in
rekall.plugins.core.FindDTB.GetAddressSpaceImplementation() based on the profile
metadata.
"""

__author__ = "Michael Cohen <scudette@google.com>"
import struct

from rekall import obj
from rekall import utils
from rekall.plugins.addrspaces import amd64
from rekall.plugins.addrspaces import intel
from rekall.plugins.windows import common

# pylint: disable=protected-access


class WindowsPagedMemoryMixin(object):
    """A mixin to implement windows specific paged memory address spaces.

    This mixin allows us to share code between 32 and 64 bit implementations.
    """
    # On windows translation pages are also valid.
    valid_mask = 1 << 11 | 1

    def __init__(self, **kwargs):
        super(WindowsPagedMemoryMixin, self).__init__(**kwargs)

        # This is the offset at which the pagefile is mapped into the physical
        # address space.
        self.pagefile_mapping = getattr(self.base, "pagefile_offset", None)

        self._resolve_vads = True
        self._vad = None

        # We cache these bitfields in order to speed up mask calculations. We
        # derive them initially from the profile so we do not need to hardcode
        # any bit positions.
        pte = self.session.profile._MMPTE()
        self.prototype_mask = pte.u.Proto.Prototype.mask
        self.transition_mask = pte.u.Trans.Transition.mask
        self.subsection_mask = pte.u.Subsect.Subsection.mask
        self.valid_mask = pte.u.Hard.Valid.mask
        self.proto_protoaddress_mask = pte.u.Proto.ProtoAddress.mask
        self.proto_protoaddress_start = pte.u.Proto.ProtoAddress.start_bit
        self.soft_pagefilehigh_mask = pte.u.Soft.PageFileHigh.mask

        # Combined masks for faster checking.
        self.proto_transition_mask = self.prototype_mask | self.transition_mask
        self.proto_transition_valid_mask = (self.proto_transition_mask |
                                            self.valid_mask)
        self.transition_valid_mask = self.transition_mask | self.valid_mask
        self.task = None

    @property
    def vad(self):
        """Returns a cached RangedCollection() of vad ranges."""

        # If this dtb is the same as the kernel dtb - there are no vads.
        if self.dtb == self.session.GetParameter("dtb"):
            return

        # If it is already cached, just return that.
        if self._vad is not None:
            return self._vad

        # We can not run plugins in recursive context.
        if not self._resolve_vads:
            return obj.NoneObject("vads not available right now")

        try:
            # Prevent recursively calling ourselves. We might resolve Prototype
            # PTEs which end up calling plugins (like the VAD plugin) which
            # might recursively translate another Vad Prototype address. This
            # safety below ensures we cant get into infinite recursion by
            # failing more complex PTE resolution on recursive calls.
            self._resolve_vads = False

            # Try to map the dtb to a task struct so we can look up the vads.
            if self.task == None:
                # Find the _EPROCESS for this dtb - we need to consult the VAD
                # for some of the address transition.
                self.task = self.session.GetParameter("dtb2task").get(self.dtb)

            self._vad = utils.RangedCollection()
            for x in self.session.plugins.vad().GetVadsForProcess(
                    self.session.profile._EPROCESS(self.task)):
                self._vad.insert(x[0], x[1], (x[0], x[1], x[3]))

            return self._vad
        finally:
            self._resolve_vads = True

    def _ConsultVad(self, virtual_address, pte_value):
        if self.vad:
            vad_hit = self.vad.get_range(virtual_address)
            if vad_hit:
                start, _, mmvad = vad_hit

                # If the MMVAD has PTEs resolve those..
                if "FirstPrototypePte" in mmvad.members:
                    pte = mmvad.FirstPrototypePte[
                        (virtual_address - start) >> 12]

                    return "Vad", pte.u.Long.v() or 0

        # Virtual address does not exist in any VAD region.
        return "Demand Zero", pte_value

    def DeterminePTEType(self, pte_value, virtual_address):
        """Determine which type of pte this is.

        This function performs the first stage PTE resolution. PTE value is a
        hardware PTE as read from the page tables.

        Returns:
          a tuple of (description, pte_value) where description is the type of
          PTE this is, and pte_value is the value of the PTE. The PTE value can
          change if this PTE actually refers to a prototype PTE - we then read
          the destination PTE location and return its real value.

        """
        if pte_value & self.valid_mask:
            desc = "Valid"

        elif (not pte_value & self.prototype_mask and  # Not a prototype
              pte_value & self.transition_mask): # But in transition.
            desc = "Transition"

        # PTE Type is not known - we need to look it up in the vad.
        elif (pte_value & self.prototype_mask and
              self.proto_protoaddress_mask & pte_value >>
              self.proto_protoaddress_start == 0xffffffff0000):
            return self._ConsultVad(virtual_address, pte_value)

        # Regular prototype PTE.
        elif pte_value & self.prototype_mask:
            # This PTE points at the prototype PTE in pte.ProtoAddress. NOTE:
            # The prototype PTE address is specified in the kernel's address
            # space since it is allocated from pool.
            pte_value = struct.unpack("<Q", self.read(
                pte_value >> self.proto_protoaddress_start, 8))[0]

            desc = "Prototype"

        # PTE value is not known, we need to look it up in the VAD.
        elif pte_value & self.soft_pagefilehigh_mask == 0:
            return self._ConsultVad(virtual_address, pte_value)

        # Regular _MMPTE_SOFTWARE entry - look in pagefile.
        else:
            desc = "Pagefile"

        return desc, pte_value

    def ResolveProtoPTE(self, pte_value, virtual_address):
        """Second level resolution of prototype PTEs.

        This function resolves a prototype PTE. Some states must be interpreted
        differently than the first level PTE.

        Returns:
          a tuple of (Description, physical_address) where description is the
          type of the resolved PTE.
        """
        # If the prototype is Valid or in Transition, just resolve it with the
        # hardware layer.
        if pte_value & self.valid_mask:
            return "Valid", super(WindowsPagedMemoryMixin, self).get_phys_addr(
                virtual_address, pte_value)

        # Not a prototype but in transition.
        if pte_value & self.proto_transition_mask == self.transition_mask:
            return ("Transition",
                    super(WindowsPagedMemoryMixin, self).get_phys_addr(
                        virtual_address, pte_value | self.valid_mask))

        # If the target of the Prototype looks like a Prototype PTE, then it is
        # a Subsection PTE. However, We cant do anything about it because we
        # don't have the filesystem. Therefore we return an invalid page.
        if pte_value & self.prototype_mask:
            return "Subsection", None

        # Prototype PTE is a Demand Zero page
        if pte_value & self.soft_pagefilehigh_mask == 0:
            return "DemandZero", None

        # Regular _MMPTE_SOFTWARE entry - return physical offset into pagefile.
        if self.pagefile_mapping is not None:
            pte = self.session.profile._MMPTE()
            pte.u.Long = pte_value

            return "Pagefile", (
                pte.u.Soft.PageFileHigh * 0x1000 + self.pagefile_mapping +
                (virtual_address & 0xFFF))

        return "Pagefile", None

    def _get_available_PDEs(self, vaddr, pdpte_value, start):
        tmp2 = vaddr
        for pde in range(0, 0x200):
            vaddr = tmp2 | (pde << 21)

            next_vaddr = tmp2 | ((pde + 1) << 21)
            if start >= next_vaddr:
                continue

            pde_value = self.get_pde(vaddr, pdpte_value)
            if not pde_value & self.valid_mask:
                # An invalid PDE means we read the vad, i.e. it is the same as
                # an array of zero PTEs.
                for x in self._get_available_PTEs(
                        [0] * 0x200, vaddr, start=start):
                    yield x

                continue

            if self.page_size_flag(pde_value):
                yield (vaddr,
                       self.get_two_meg_paddr(vaddr, pde_value),
                       0x200000)
                continue

            # This reads the entire PTE table at once - On
            # windows where IO is extremely expensive, its
            # about 10 times more efficient than reading it
            # one value at the time - and this loop is HOT!
            pte_table_addr = ((pde_value & 0xffffffffff000) |
                              ((vaddr & 0x1ff000) >> 9))

            data = self.base.read(pte_table_addr, 8 * 0x200)
            pte_table = struct.unpack("<" + "Q" * 0x200, data)

            for x in self._get_available_PTEs(
                    pte_table, vaddr, start=start):
                yield x

    def _get_available_PTEs(self, pte_table, vaddr, start=0):
        """Scan the PTE table and yield address ranges which are valid."""
        tmp = vaddr
        if self.vad:
            vads = sorted([(x[0], x[1]) for x in self.vad.collection],
                          reverse=True)
        else:
            vads = []

        for i in xrange(0, len(pte_table)):
            pfn = i << 12
            pte_value = pte_table[i]

            vaddr = tmp | pfn
            next_vaddr = tmp | (pfn + 0x1000)
            if start >= next_vaddr:
                continue

            # Remove all the vads that end below this address. This optimization
            # allows us to skip DemandZero pages which occur outsize the VAD
            # ranges.
            if vads:
                while vads and vads[-1][1] < vaddr:
                    vads.pop(-1)

                # Address is below the next available vad's start. We are not
                # inside a vad range and a 0 PTE is unmapped.
                if (pte_value == 0 and vads and
                        vaddr < vads[-1][0]):
                    continue

            elif pte_value == 0:
                continue

            phys_addr = self.get_phys_addr(vaddr, pte_value)

            # Only yield valid physical addresses. This will skip DemandZero
            # pages and File mappings into the filesystem.
            if phys_addr is not None:
                yield (vaddr, phys_addr, 0x1000)

    def get_phys_addr(self, virtual_address, pte_value):
        """First level resolution of PTEs.

        pte_value must be the actual PTE from hardware page tables (Not software
        PTEs which are prototype PTEs).
        """
        desc, pte_value = self.DeterminePTEType(pte_value, virtual_address)

        # Transition pages can be treated as Valid, let the hardware resolve
        # it.
        if desc == "Transition" or desc == "Valid":
            return super(WindowsPagedMemoryMixin, self).get_phys_addr(
                virtual_address, pte_value | self.valid_mask)

        if desc == "Prototype":
            return self.ResolveProtoPTE(pte_value, virtual_address)[1]

        # This is a prototype into a vad region.
        elif desc == "Vad":
            return self.ResolveProtoPTE(pte_value, virtual_address)[1]

        elif desc == "Pagefile" and self.pagefile_mapping:
            pte = self.session.profile._MMPTE()
            pte.u.Long = pte_value

            return (pte.u.Soft.PageFileHigh * 0x1000 +
                    self.pagefile_mapping + (virtual_address & 0xFFF))


class WindowsIA32PagedMemoryPae(WindowsPagedMemoryMixin,
                                intel.IA32PagedMemoryPae):
    """A Windows specific IA32PagedMemoryPae."""

    def vtop(self, vaddr):
        '''Translates virtual addresses into physical offsets.

        The function should return either None (no valid mapping) or the offset
        in physical memory where the address maps.
        '''
        vaddr = int(vaddr)
        try:
            return self._tlb.Get(vaddr)
        except KeyError:
            pdpte = self.get_pdpte(vaddr)
            if not pdpte & self.valid_mask:
                return None

            pde = self.get_pde(vaddr, pdpte)
            if not pde & self.valid_mask:
                # If PDE is not valid the page table does not exist
                # yet. According to
                # http://i-web.i.u-tokyo.ac.jp/edu/training/ss/lecture/new-documents/Lectures/14-AdvVirtualMemory/AdvVirtualMemory.pdf
                # slide 11 this is the same as PTE of zero - i.e. consult the
                # VAD.
                if not self._resolve_vads:
                    return None

                return self.get_phys_addr(vaddr, 0)

            if self.page_size_flag(pde):
                return self.get_two_meg_paddr(vaddr, pde)

            pte = self.get_pte(vaddr, pde)
            res = self.get_phys_addr(vaddr, pte)

            self._tlb.Put(vaddr, res)
            return res


class WindowsAMD64PagedMemory(WindowsPagedMemoryMixin, amd64.AMD64PagedMemory):
    """A windows specific AMD64PagedMemory.

    Implements support for reading the pagefile if the base address space
    contains a pagefile.
    """

    def vtop(self, vaddr):
        '''Translates virtual addresses into physical offsets.

        The function returns either None (no valid mapping) or the offset in
        physical memory where the address maps.
        '''
        try:
            return self._tlb.Get(vaddr)
        except KeyError:
            vaddr = long(vaddr)
            pml4e = self.get_pml4e(vaddr)
            if not pml4e & self.valid_mask:
                # Add support for paged out PML4E
                return None

            pdpte = self.get_pdpte(vaddr, pml4e)
            if not pdpte & self.valid_mask:
                # Add support for paged out PDPTE
                # Insert buffalo here!
                return None

            if self.page_size_flag(pdpte):
                return self.get_one_gig_paddr(vaddr, pdpte)

            pde = self.get_pde(vaddr, pdpte)
            if not pde & self.valid_mask:
                # If PDE is not valid the page table does not exist
                # yet. According to
                # http://i-web.i.u-tokyo.ac.jp/edu/training/ss/lecture/new-documents/Lectures/14-AdvVirtualMemory/AdvVirtualMemory.pdf
                # slide 11 this is the same PTE of zero.
                if not self._resolve_vads:
                    return None

                return self.get_phys_addr(vaddr, 0)

            # Is this a 2 meg page?
            if self.page_size_flag(pde):
                return self.get_two_meg_paddr(vaddr, pde)

            pte = self.get_pte(vaddr, pde)
            res = self.get_phys_addr(vaddr, pte)

            self._tlb.Put(vaddr, res)
            return res


class Pagefiles(common.WindowsCommandPlugin):
    """Report all the active pagefiles."""

    name = "pagefiles"

    def render(self, renderer):
        pagingfiles = self.profile.get_constant_object(
            'MmPagingFile',
            target='Array', target_args=dict(
                target='Pointer',
                count=16,
                target_args=dict(
                    target='_MMPAGING_FILE'
                    )
                )
            )

        renderer.table_header([
            ('_MMPAGING_FILE', '', '[addrpad]'),
            ('Number', 'number', '>3'),
            ('Size (b)', 'size', '>10'),
            ('Filename', 'filename', '20'),
            ])

        for pf in pagingfiles:
            if pf:
                renderer.table_row(
                    pf, pf.PageFileNumber, pf.Size * 0x1000, pf.PageFileName)
