# Rekall Memory Forensics
# Copyright (C) 2012 Michael Cohen
# Copyright 2013 Google Inc. All Rights Reserved.
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

"""This module implements core plugins."""

__author__ = "Michael Cohen <scudette@gmail.com>"

import inspect
import pdb
import re
import os
import textwrap

from rekall import addrspace
from rekall import args
from rekall import config
from rekall import constants
from rekall import registry
from rekall import plugin
from rekall import obj
from rekall import testlib
from rekall import utils


class Info(plugin.Command):
    """Print information about various subsystems."""

    __name = "info"

    standard_options = []

    def __init__(self, item=None, verbosity=0, **kwargs):
        """Display information about a plugin.

        Args:
          item: The plugin class to examine.
          verbosity: How much information to display.
        """
        super(Info, self).__init__(**kwargs)
        self.item = item
        self.verbosity = verbosity

    def plugins(self):
        for name, cls in plugin.Command.classes.items():
            if name:
                doc = cls.__doc__ or " "
                yield name, cls.name, doc.splitlines()[0]

    def profiles(self):
        for name, cls in obj.Profile.classes.items():
            if self.verbosity == 0 and not cls.metadata("os"):
                continue

            if name:
                yield name, cls.__doc__.splitlines()[0].strip()

    def address_spaces(self):
        for name, cls in addrspace.BaseAddressSpace.classes.items():
            yield dict(name=name, function=cls.name, definition=cls.__module__)

    def render(self, renderer):
        if self.item is None:
            return self.render_general_info(renderer)
        else:
            return self.render_item_info(self.item, renderer)

    def _split_into_paragraphs(self, string, dedent):
        """Split a string into paragraphs.

        A paragraph is defined as lines of text having the same indentation. An
        empty new line breaks the paragraph.

        The first line in each paragraph is allowed to be indented more than the
        second line.
        """
        paragraph = []
        last_leading_space = 0
        first_line_indent = 0

        for line in string.splitlines():
            line = line[dedent:]

            m = re.match(r"\s*", line)
            leading_space = len(m.group(0))

            text = line[leading_space:]

            # First line is always included.
            if not paragraph:
                paragraph = [text]
                first_line = True
                first_line_indent = leading_space
                continue

            if first_line and last_leading_space != leading_space:
                if text:
                    paragraph.append(text)

                last_leading_space = leading_space
                first_line = False

            elif leading_space != last_leading_space:
                if paragraph:
                    yield paragraph, first_line_indent

                paragraph = []
                if text:
                    paragraph.append(text)
                last_leading_space = leading_space
                first_line_indent = leading_space
                first_line = True
            else:
                if text:
                    paragraph.append(text)

                first_line = False

        if paragraph:
            yield paragraph, first_line_indent

    def split_into_paragraphs(self, string, dedent=0, wrap=50):
        for paragraph, leading_space in self._split_into_paragraphs(
                string, dedent):
            paragraph = textwrap.wrap("\n".join(paragraph), wrap)
            yield "\n".join([(" " * leading_space + x) for x in paragraph])

    def parse_args_string(self, arg_string):
        """Parses a standard docstring into args and docs for each arg."""
        parameter = None
        doc = ""

        for line in arg_string.splitlines():
            m = re.match(r"\s+([^\s]+):(.+)", line)
            if m:
                if parameter:
                    yield parameter, doc

                parameter = m.group(1)
                doc = m.group(2)
            else:
                doc += "\n" + line

        if parameter:
            yield parameter, doc

    def get_default_args(self, item=None):
        if item is None:
            item = self.item

        metadata = config.CommandMetadata(item)
        for x, y in metadata.args.items():
            # Normalize the option name to use _.
            x = x.replace("-", "_")

            yield x, self._clean_up_doc(y.get("help", ""))

    def render_item_info(self, item, renderer):
        """Render information about the specific item."""
        cls_doc = inspect.cleandoc(item.__doc__ or " ")
        init_doc = inspect.cleandoc(
            (item.__init__.__doc__ or " ").split("Args:")[0])

        if isinstance(item, registry.MetaclassRegistry):
            # show the args it takes. Relies on the docstring to be formatted
            # properly.
            doc_string = cls_doc + init_doc
            doc_string += (
                "\n\nLink:\n"
                "http://www.rekall-forensic.com/epydocs/%s.%s-class.html"
                "\n\n" % (item.__module__, item.__name__))

            renderer.write(doc_string)

            renderer.table_header([('Parameter', 'parameter', '30'),
                                   ('Documentation', 'doc', '70')])
            for parameter, doc in self.get_default_args(item):
                renderer.table_row(parameter, doc)

            # Add the standard help options.
            for parameter, descriptor in self.standard_options:
                renderer.table_row(parameter, self._clean_up_doc(descriptor))

        else:
            # For normal objects just write their docstrings.
            renderer.write(item.__doc__ or " ")

        renderer.write("\n")

    def _clean_up_doc(self, doc, dedent=0):
        clean_doc = []
        for paragraph in self.split_into_paragraphs(
                " " * dedent + doc, dedent=dedent, wrap=70):
            clean_doc.append(paragraph)

        return "\n".join(clean_doc)

    def render_general_info(self, renderer):
        renderer.write(constants.BANNER)
        renderer.section()
        renderer.table_header([('Command', 'function', "20"),
                               ('Provider Class', 'provider', '20'),
                               ('Docs', 'docs', '50'),
                              ])

        for cls, name, doc in sorted(self.plugins(), key=lambda x: x[1]):
            renderer.table_row(name, cls, doc)


class TestInfo(testlib.DisabledTest):
    """Disable the Info test."""

    PARAMETERS = dict(commandline="info")


class FindDTB(plugin.PhysicalASMixin, plugin.ProfileCommand):
    """A base class to be used by all the FindDTB implementation."""
    __abstract = True

    def dtb_hits(self):
        """Yields hits for the DTB offset."""
        return []

    def VerifyHit(self, hit):
        """Verify the hit for correctness, yielding an address space."""
        return self.CreateAS(hit)

    def address_space_hits(self):
        """Finds DTBs and yields virtual address spaces that expose kernel.

        Yields:
          BaseAddressSpace-derived instances, validated using the
          verify_address_space() method..
        """
        for hit in self.dtb_hits():
            address_space = self.VerifyHit(hit)
            if address_space is not None:
                yield address_space

    def CreateAS(self, dtb):
        """Creates an address space from this hit."""
        address_space_cls = self.GetAddressSpaceImplementation()
        try:
            return address_space_cls(
                base=self.physical_address_space,
                dtb=dtb, session=self.session,
                profile=self.profile)
        except IOError:
            return None

    def GetAddressSpaceImplementation(self):
        """Returns the correct address space class for this profile."""
        # The virtual address space implementation is chosen by the profile.
        architecture = self.profile.metadata("arch")
        if architecture == "AMD64":
            impl = "AMD64PagedMemory"

        # PAE profiles go with the pae address space.
        elif architecture == "I386" and self.profile.metadata("pae"):
            impl = 'IA32PagedMemoryPae'

            # Add specific support for windows.
            if self.profile.metadata("os") == "windows":
                impl = "WindowsIA32PagedMemoryPae"

        elif architecture == "MIPS":
            impl = "MIPS32PagedMemory"

        else:
            impl = 'IA32PagedMemory'


        as_class = addrspace.BaseAddressSpace.classes[impl]
        return as_class


class LoadAddressSpace(plugin.Command):
    """Load address spaces into the session if its not already loaded."""

    __name = "load_as"

    def __init__(self, pas_spec="auto", **kwargs):
        """Tries to create the address spaces and assigns them to the session.

        An address space specification is a column delimited list of AS
        constructors which will be stacked. For example:

        FileAddressSpace:EWF

        if the specification is "auto" we guess by trying every combintion until
        a virtual AS is obtained.

        The virtual address space is chosen based on the profile.

        Args:
          pas_spec: A Physical address space specification.
        """
        super(LoadAddressSpace, self).__init__(**kwargs)
        self.pas_spec = pas_spec

    # Parse Address spaces from this specification. TODO: Support EPT
    # specification and nesting.
    ADDRESS_SPACE_RE = re.compile("([a-zA-Z0-9]+)@((0x)?[0-9a-zA-Z]+)")
    def ResolveAddressSpace(self, name=None):
        """Resolve the name into an address space.

        This function is intended to be called from plugins which allow an
        address space to be specified on the command line. We implement a simple
        way for the user to specify the address space using a string. The
        following formats are supported:

        Kernel, K : Represents the kernel address space.
        Physical, P: Represents the physical address space.

        as_type@dtb_address: Instantiates the address space at the specified
            DTB. For example: amd64@0x18700

        pid@pid_number: Use the process address space for the specified pid.
        """
        if name is None:
            result = self.session.GetParameter("default_address_space")
            if result:
                return result

            name = "K"

        # We can already specify a proper address space here.
        if isinstance(name, addrspace.BaseAddressSpace):
            return name

        if name == "K" or name == "Kernel":
            return (self.session.kernel_address_space or
                    self.GetVirtualAddressSpace())

        if name == "P" or name == "Physical":
            return (self.session.physical_address_space or
                    self.GetPhysicalAddressSpace())

        m = self.ADDRESS_SPACE_RE.match(name)
        if m:
            arg = int(m.group(2), 0)
            if m.group(1) == "pid":
                for task in self.session.plugins.pslist(
                        pid=arg).filter_processes():
                    return task.get_process_address_space()
                raise AttributeError("Process pid %s not found" % arg)

            as_cls = addrspace.BaseAddressSpace.classes.get(m.group(1))
            if as_cls:
                return as_cls(session=self.session, dtb=arg,
                              base=self.GetPhysicalAddressSpace())

        raise AttributeError("Address space specification %r invalid.", name)

    def GetPhysicalAddressSpace(self):
        try:
            # Try to get a physical address space.
            if self.pas_spec == "auto":
                self.session.physical_address_space = self.GuessAddressSpace()
            else:
                self.session.physical_address_space = self.AddressSpaceFactory(
                    specification=self.pas_spec)


            return self.session.physical_address_space

        except addrspace.ASAssertionError as e:
            self.session.logging.error("Could not create address space: %s" % e)

        return self.session.physical_address_space

    # TODO: Deprecate this method completely since it is rarely used.
    def GetVirtualAddressSpace(self, dtb=None):
        """Load the Kernel Virtual Address Space.

        Note that this function is usually not used since the Virtual AS is now
        loaded from guess_profile.ApplyFindDTB() when profiles are guessed. This
        function is only used when the profile is directly provided by the user.
        """
        if dtb is None:
            dtb = self.session.GetParameter("dtb")

        if not self.session.physical_address_space:
            self.GetPhysicalAddressSpace()

        if not self.session.physical_address_space:
            raise plugin.PluginError("Unable to find physical address space.")

        self.profile = self.session.profile
        if self.profile == None:
            raise plugin.PluginError(
                "Must specify a profile to load virtual AS.")

        # If we know the DTB, just build the address space.
        # Otherwise, delegate to a find_dtb plugin.
        find_dtb = self.session.plugins.find_dtb()
        if find_dtb == None:
            return find_dtb

        if dtb:
            self.session.kernel_address_space = find_dtb.CreateAS(dtb)

        else:
            self.session.logging.debug("DTB not specified. Delegating to "
                                       "find_dtb.")
            for address_space in find_dtb.address_space_hits():
                with self.session:
                    self.session.kernel_address_space = address_space
                    self.session.SetCache("dtb", address_space.dtb)
                    break

            if self.session.kernel_address_space is None:
                self.session.logging.info(
                    "A DTB value was found but failed to verify. "
                    "Some troubleshooting steps to consider: "
                    "(1) Is the profile correct? (2) Is the KASLR correct? "
                    "Try running the find_kaslr plugin on systems that "
                    "use KASLR and see if there are more possible values. "
                    "You can specify which offset to use using "
                    "--vm_kernel_slide. (3) If you know the DTB, for "
                    "example from knowing the value of the CR3 register "
                    "at time of acquisition, you can set it using --dtb. "
                    "On most 64-bit systems, you can use the DTB of any "
                    "process, not just the kernel!")
                raise plugin.PluginError(
                    "A DTB value was found but failed to verify. "
                    "See logging messages for more information.")

        # Set the default address space for plugins like disassemble and dump.
        if not self.session.GetParameter("default_address_space"):
            self.session.SetCache(
                "default_address_space", self.session.kernel_address_space)

        return self.session.kernel_address_space

    def GuessAddressSpace(self, base_as=None, **kwargs):
        """Loads an address space by stacking valid ASes on top of each other
        (priority order first).
        """
        base_as = base_as
        error = addrspace.AddrSpaceError()

        address_spaces = sorted(addrspace.BaseAddressSpace.classes.values(),
                                key=lambda x: x.order)

        while 1:
            self.session.logging.debug("Voting round with base: %s", base_as)
            found = False
            for cls in address_spaces:
                # Only try address spaces which claim to support images.
                if not cls.metadata("image"):
                    continue

                self.session.logging.debug("Trying %s ", cls)
                try:
                    base_as = cls(base=base_as, session=self.session,
                                  **kwargs)
                    self.session.logging.debug("Succeeded instantiating %s",
                                               base_as)
                    found = True
                    break
                except (AssertionError,
                        addrspace.ASAssertionError) as e:
                    self.session.logging.debug("Failed instantiating %s: %s",
                                               cls.__name__, e)
                    error.append_reason(cls.__name__, e)
                    continue
                except Exception as e:
                    self.session.logging.error("Fatal Error: %s", e)
                    if self.session.GetParameter("debug"):
                        pdb.post_mortem()

                    raise

            ## A full iteration through all the classes without anyone
            ## selecting us means we are done:
            if not found:
                break

        if base_as:
            self.session.logging.info("Autodetected physical address space %s",
                                      base_as)

        return base_as

    def AddressSpaceFactory(self, specification='', **kwargs):
        """Build the address space from the specification.

        Args:
           specification: A column separated list of AS class names to be
           stacked.
        """
        base_as = None
        for as_name in specification.split(":"):
            as_cls = addrspace.BaseAddressSpace.classes.get(as_name)
            if as_cls is None:
                raise addrspace.Error("No such address space %s" % as_name)

            base_as = as_cls(base=base_as, session=self.session, **kwargs)

        return base_as

    def render(self, renderer):
        if not self.session.physical_address_space:
            self.GetPhysicalAddressSpace()

        if not self.session.kernel_address_space:
            self.GetVirtualAddressSpace()


class OutputFileMixin(object):
    """A mixin for plugins that want to dump a single user controlled output."""
    @classmethod
    def args(cls, parser):
        """Declare the command line args we need."""
        super(OutputFileMixin, cls).args(parser)
        parser.add_argument("out_file",
                            help="Path for output file.")

    def __init__(self, out_file=None, **kwargs):
        super(OutputFileMixin, self).__init__(**kwargs)
        self.out_file = out_file
        if out_file is None:
            raise RuntimeError("An output must be provided.")


class DirectoryDumperMixin(object):
    """A mixin for plugins that want to dump files to a directory."""

    # Set this to False if the dump_dir parameter is mandatory.
    dump_dir_optional = True
    default_dump_dir = "."

    @classmethod
    def args(cls, parser):
        """Declare the command line args we need."""
        super(DirectoryDumperMixin, cls).args(parser)
        help = "Path suitable for dumping files."
        if cls.dump_dir_optional:
            help += " (Default: Use current directory)"
        else:
            help += " (Required)"

        parser.add_argument("-D", "--dump_dir", default=None,
                            required=not cls.dump_dir_optional,
                            help=help)

    def __init__(self, *args_, **kwargs):
        """Dump to a directory.

        Args:
          dump_dir: The directory where files should be dumped.
        """
        dump_dir = kwargs.pop("dump_dir", None)
        super(DirectoryDumperMixin, self).__init__(*args_, **kwargs)

        self.dump_dir = (dump_dir or self.default_dump_dir or
                         self.session.GetParameter("dump_dir"))

        self.check_dump_dir(self.dump_dir)

    def check_dump_dir(self, dump_dir=None):
        # If the dump_dir parameter is not optional insist its there.
        if not self.dump_dir_optional and not dump_dir:
            raise plugin.PluginError(
                "Please specify a dump directory.")

        if dump_dir and not os.path.isdir(dump_dir):
            raise plugin.PluginError("%s is not a directory" % self.dump_dir)

    def CopyToFile(self, address_space, start, end, outfd):
        """Copy a part of the address space to the output file.

        This utility function allows the writing of sparse files correctly. We
        pass over the address space, automatically skipping regions which are
        not valid. For file systems which support sparse files (e.g. in Linux),
        no additional disk space will be used for unmapped regions.

        If a region has no mapped pages, the resulting file will be of 0 bytes
        long.
        """
        BUFFSIZE = 1024 * 1024

        for offset, _, length in address_space.get_available_addresses(
                start=start):

            if start > offset:
                continue

            if offset >= end:
                break

            out_offset = offset - start
            self.session.report_progress("Dumping %s Mb", out_offset/BUFFSIZE)
            outfd.seek(out_offset)
            i = offset

            # Now copy the region in fixed size buffers.
            while i < offset + length:
                to_read = min(BUFFSIZE, length)

                data = address_space.read(i, to_read)
                outfd.write(data)

                i += to_read


class Null(plugin.Command):
    """This plugin does absolutely nothing.

    It is used to measure startup overheads.
    """
    __name = "null"

    def render(self, renderer):
        _ = renderer


class LoadPlugins(plugin.Command):
    """Load user provided plugins.

    This probably is only useful after the interactive shell started since you
    can already use the --plugin command line option.
    """

    __name = "load_plugin"
    interactive = True

    def __init__(self, path, **kwargs):
        super(LoadPlugins, self).__init__(**kwargs)
        if isinstance(path, basestring):
            path = [path]

        args.LoadPlugins(path)


class Printer(plugin.Command):
    """A plugin to print an object."""

    __name = "p"
    interactive = True

    def __init__(self, target=None, **kwargs):
        """Prints an object to the screen."""
        super(Printer, self).__init__(**kwargs)
        self.target = target

    def render(self, renderer):
        for line in utils.SmartStr(self.target).splitlines():
            renderer.format("{0}\n", line)


class Lister(Printer):
    """A plugin to list objects."""

    __name = "l"
    interactive = True

    def render(self, renderer):
        if self.target is None:
            self.session.logging.error("You must list something.")
            return

        for item in self.target:
            self.session.plugins.p(target=item).render(renderer)


class DT(plugin.ProfileCommand):
    """Print a struct or other symbol.

    Really just a convenience function for instantiating the object and printing
    all its members.
    """

    __name = "dt"

    @classmethod
    def args(cls, parser):
        super(DT, cls).args(parser)
        parser.add_argument("target",
                            help="Name of a struct definition.")

        parser.add_argument("offset", type="IntParser", default=0,
                            required=False, help="Name of a struct definition.")

        parser.add_argument("-a", "--address-space", default=None,
                            help="The address space to use.")

    def __init__(self, target=None, offset=0, address_space=None,
                 **kwargs):
        """Prints an object to the screen."""
        super(DT, self).__init__(**kwargs)

        self.offset = offset
        self.target = target
        if target is None:
            raise plugin.PluginError("You must specify something to print.")

        load_as = self.session.plugins.load_as(session=self.session)
        self.address_space = load_as.ResolveAddressSpace(address_space)

        if isinstance(target, basestring):
            self.target = self.profile.Object(
                target, offset=self.offset, vm=self.address_space)

    def render_Struct(self, renderer, struct):
        renderer.format(
            "[{0} {1}] @ {2:addrpad} \n",
            struct.obj_type, struct.obj_name or '', struct.obj_offset)

        renderer.table_header([
            dict(name="Offset", type="TreeNode", max_depth=5,
                 child=dict(style="address"), width=20),
            ("Field", "field", "30"),
            dict(name="Content", cname="content", style="typed")])

        self._render_Struct(renderer, struct)

    def _render_Struct(self, renderer, struct, depth=0):
        fields = []
        # Print all the fields sorted by offset within the struct.
        for k in set(struct.members).union(struct.callable_members):
            member = getattr(struct, k)
            base_member = struct.m(k)

            offset = base_member.obj_offset - struct.obj_offset
            if offset == None:  # NoneObjects screw up sorting order here.
                offset = -1

            fields.append((offset, k, member))

        for offset, k, v in sorted(fields):
            renderer.table_row(offset, k, v, depth=depth)
            if isinstance(v, obj.Struct):
                self._render_Struct(renderer, v, depth=depth+1)

    def render(self, renderer):
        item = self.target

        if isinstance(item, obj.Pointer):
            item = item.deref()

        if isinstance(item, obj.Struct):
            return self.render_Struct(renderer, item)

        self.session.plugins.p(self.target).render(renderer)


class AddressMap(object):
    """Label memory ranges."""
    _COLORS = u"BLACK RED GREEN YELLOW BLUE MAGENTA CYAN WHITE".split()

    # All color combinations except those with the same foreground an background
    # colors, since these will be invisible.
    COLORS = []
    for x in _COLORS:
        for y in _COLORS:
            if x != y:
                COLORS.append((x, y))

    def __init__(self):
        self.collection = utils.RangedCollection()
        self.idx = 0
        self.label_color_map = {}

    def AddRange(self, start, end, label):
        try:
            fg, bg = self.label_color_map[label]
        except KeyError:
            fg, bg = self.COLORS[self.idx]
            self.idx = (self.idx + 1) % len(self.COLORS)
            self.label_color_map[label] = (fg, bg)

        self.collection.insert(start, end, (label, fg, bg))

    def HighlightRange(self, start, end, relative=True):
        """Returns a highlighting list from start address to end.

        If relative is True the highlighting list is relative to the start
        offset.
        """
        result = []
        for i in range(start, end):
            hit = self.collection.get_range(i)
            if hit:
                _, fg, bg = hit
                if relative:
                    i -= start

                result.append([i, i+1, fg, bg])

        return result

    def GetComment(self, start, end):
        """Returns a tuple of labels and their highlights."""
        labels = []
        for i in range(start, end):
            hit = self.collection.get_range(i)
            if hit:
                if hit not in labels:
                    labels.append(hit)

        result = ""
        highlights = []
        for label, fg, bg in labels:
            highlights.append((len(result), len(result) + len(label), fg, bg))
            result += label + ", "

        # Drop the last ,
        if result:
            result = result[:-2]

        return utils.AttributedString(result, highlights=highlights)


class Dump(plugin.Command):
    """Hexdump an object or memory location."""

    __name = "dump"

    @classmethod
    def args(cls, parser):
        super(Dump, cls).args(parser)
        parser.add_argument("offset", type="SymbolAddress",
                            help="An offset to hexdump.")

        parser.add_argument("-a", "--address_space", default=None,
                            help="The address space to use.")

        parser.add_argument("--data", default=None,
                            help="Dump this string instead.")

        parser.add_argument("--length", default=None, type="IntParser",
                            help="Maximum length to dump.")

        parser.add_argument("--suppress_headers", default=False, type="Boolean",
                            help="Should headers be suppressed?.")


    def __init__(self, offset=0, address_space=None, data=None, length=None,
                 width=None, rows=None, suppress_headers=False,
                 address_map=None, **kwargs):
        """Hexdump an object or memory location.

        You can use this plugin repeateadely to keep dumping more data using the
        "p _" (print last result) operation:

        In [2]: dump 0x814b13b0, address_space="K"
        ------> dump(0x814b13b0, address_space="K")
        Offset                         Hex                              Data
        ---------- ------------------------------------------------ ----------------
        0x814b13b0 03 00 1b 00 00 00 00 00 b8 13 4b 81 b8 13 4b 81  ..........K...K.

        Out[3]: <rekall.plugins.core.Dump at 0x2967510>

        In [4]: p _
        ------> p(_)
        Offset                         Hex                              Data
        ---------- ------------------------------------------------ ----------------
        0x814b1440 70 39 00 00 54 1b 01 00 18 0a 00 00 32 59 00 00  p9..T.......2Y..
        0x814b1450 6c 3c 01 00 81 0a 00 00 18 0a 00 00 00 b0 0f 06  l<..............
        0x814b1460 00 10 3f 05 64 77 ed 81 d4 80 21 82 00 00 00 00  ..?.dw....!.....

        Args:
          offset: The offset to start dumping from.

          address_space: The address_space to dump from. If omitted we use the
            default address space.

          data: If provided we dump the string provided in data rather than use
            an address_space.

          length: If provided we stop dumping at the specified length.

          width: How many Hex character per line.

          rows: How many rows to dump.

          suppress_headers: If set we do not write the headers.

        """
        super(Dump, self).__init__(**kwargs)

        # Allow offset to be symbol name.
        if isinstance(offset, basestring):
            self.offset = self.session.address_resolver.get_address_by_name(
                offset)

        elif isinstance(offset, obj.BaseObject):
            self.offset = offset.obj_offset
            address_space = offset.obj_vm
            length = offset.obj_size
        else:
            self.offset = obj.Pointer.integer_to_address(offset)

        self.length = length

        # default width can be set in the session.
        if width is None:
            width = self.session.GetParameter("hexdump_width", 16)

        self.width = int(width)
        if rows is None:
            rows = self.session.GetParameter("paging_limit") or 30

        self.rows = int(rows)
        self.suppress_headers = suppress_headers
        self.address_map = address_map or AddressMap()

        if data is not None:
            address_space = addrspace.BufferAddressSpace(
                data=data, session=self.session)

            if self.length is None:
                self.length = len(data)

        # Resolve the correct address space. This allows the address space to be
        # specified from the command line (e.g.
        load_as = self.session.plugins.load_as()
        self.address_space = load_as.ResolveAddressSpace(address_space)

    def render(self, renderer):
        if self.offset == None:
            renderer.format("Error: {0}\n", self.offset.reason)
            return

        to_read = min(self.width * self.rows,
                      self.address_space.end() - self.offset)

        if self.length is not None:
            to_read = min(to_read, self.length)

        renderer.table_header(
            [("Offset", "offset", "[addr]"),
             dict(name="Data", style="hexdump", hex_width=self.width),
             ("Comment", "comment", "40")],
            suppress_headers=self.suppress_headers)

        resolver = self.session.address_resolver
        for offset in range(self.offset, self.offset + to_read):
            comment = resolver.format_address(offset, max_distance=1)
            if comment:
                self.address_map.AddRange(offset, offset + 1, comment)

        offset = self.offset
        for offset in range(self.offset, self.offset + to_read, self.width):
            # Add a symbol name for the start of each row.
            hex_data = utils.HexDumpedString(
                self.address_space.read(offset, self.width),
                highlights=self.address_map.HighlightRange(
                    offset, offset + self.width, relative=True))

            comment = self.address_map.GetComment(offset, offset + self.width)

            renderer.table_row(offset, hex_data, comment, nowrap=True)

        # Advance the offset so we can continue from this offset next time we
        # get called.
        self.offset = offset


class Grep(plugin.Command):
    """Search an address space for keywords."""

    __name = "grep"

    @classmethod
    def args(cls, parser):
        super(Grep, cls).args(parser)
        parser.add_argument("--address_space", default="Kernel",
                            help="Name of the address_space to search.")

        parser.add_argument("--offset", default=0, type="IntParser",
                            help="Start searching from this offset.")

        parser.add_argument("keyword",
                            help="The binary string to find.")

        parser.add_argument("--limit", default=1024*1024,
                            help="The length of data to search.")

    def __init__(self, offset=0, keyword=None, context=20, address_space=None,
                 limit=1024 * 1024, **kwargs):
        """Search an address space for keywords.

        Args:
          address_space: Name of the address_space to search.
          offset: Start searching from this offset.
          keyword: The binary string to find.
          limit: The length of data to search.
        """
        super(Grep, self).__init__(**kwargs)
        self.keyword = keyword
        self.context = context
        self.offset = offset
        self.limit = limit
        load_as = self.session.plugins.load_as(session=self.session)
        self.address_space = load_as.ResolveAddressSpace(address_space)

    def _GenerateHits(self, data):
        start = 0
        while 1:
            idx = data.find(self.keyword, start)
            if idx == -1:
                break

            yield idx
            start = idx + 1

    def render(self, renderer):
        renderer.table_header([("Offset", "offset", "[addr]"),
                               ("Hex", "hex", str(3 * self.context)),
                               ("Data", "data", str(self.context)),
                               ("Comment", "comment", "40")]
                             )

        resolver = self.session.address_resolver
        offset = self.offset
        while offset < self.offset + self.limit:
            data = self.address_space.read(offset, 4096)
            for idx in self._GenerateHits(data):
                for dump_offset, hexdata, translated_data in utils.Hexdump(
                        data[idx-20:idx+20], width=self.context):
                    comment = resolver.format_address(offset + idx,
                                                      max_distance=1e6)

                    renderer.table_row(
                        offset + idx - 20 + dump_offset,
                        hexdata, "".join(translated_data),
                        comment)

            offset += len(data)

        self.offset = offset



class MemmapMixIn(object):
    """A Mixin to create the memmap plugins for all the operating systems."""

    @classmethod
    def args(cls, parser):
        """Declare the command line args we need."""
        super(MemmapMixIn, cls).args(parser)
        parser.add_argument(
            "--coalesce", default=False, type="Boolean",
            help="Merge contiguous pages into larger ranges.")

        parser.add_argument(
            "--all", default=False, type="Boolean",
            help="Use the entire range of address space.")

    def __init__(self, *pos_args, **kwargs):
        """Calculates the memory regions mapped by a process or the kernel.

        If no process filtering directives are provided, enumerates the kernel
        address space.
        """
        self.coalesce = kwargs.pop("coalesce", False)
        self.all = kwargs.pop("all", False)
        super(MemmapMixIn, self).__init__(*pos_args, **kwargs)

    def _render_map(self, task_space, renderer, highest_address):
        renderer.format(u"Dumping address space at DTB {0:#x}\n\n",
                        task_space.dtb)

        renderer.table_header([("Virtual", "offset_v", "[addrpad]"),
                               ("Physical", "offset_p", "[addrpad]"),
                               ("Size", "process_size", "[addr]")])

        if self.coalesce:
            ranges = task_space.get_address_ranges()
        else:
            ranges = task_space.get_available_addresses()

        for virtual_address, phys_address, length in ranges:
            # When dumping out processes do not dump the kernel.
            if not self.all and virtual_address > highest_address:
                break

            renderer.table_row(virtual_address, phys_address, length)

    def _get_highest_user_address(self):
        """Returns the highest process address to display.

        This is operating system dependent.
        """
        return 2**64

    def render(self, renderer):
        if not self.filtering_requested:
            # Dump the entire kernel address space.
            return self._render_map(self.kernel_address_space, renderer, 2**64)

        for task in self.filter_processes():
            renderer.section()
            renderer.RenderProgress("Dumping pid {0}".format(task.pid))

            task_space = task.get_process_address_space()
            renderer.format(u"Process: '{0}' pid: {1:6}\n\n",
                            task.name, task.pid)

            if not task_space:
                renderer.write("Unable to read pages for task.\n")
                continue

            self._render_map(task_space, renderer,
                             self._get_highest_user_address())


class SetProcessContextMixin(object):
    """Set the current process context.

    The basic functionality of all platforms' cc plugin.
    """

    name = "cc"
    interactive = True

    def __enter__(self):
        """Use this plugin as a context manager.

        When used as a context manager we save the state of the address resolver
        and then restore it on exit. This prevents the address resolver from
        losing its current state and makes switching contexts much faster.
        """
        self.process_context = self.session.GetParameter("process_context")
        return self

    def __exit__(self, unused_type, unused_value, unused_traceback):
        # Restore the process context.
        self.SwitchProcessContext(self.process_context)

    def SwitchProcessContext(self, process=None):
        if process == None:
            message = "Switching to Kernel context"
            self.session.SetCache("default_address_space",
                                  self.session.kernel_address_space)
        else:
            message = ("Switching to process context: {0} "
                       "(Pid {1}@{2:#x})").format(
                           process.name, process.pid, process)

            self.session.SetCache(
                "default_address_space",
                process.get_process_address_space() or None)

        # Reset the address resolver for the new context.
        self.session.SetCache("process_context", process)
        self.session.logging.debug(message)

        return message

    def SwitchContext(self):
        if not self.filtering_requested:
            return self.SwitchProcessContext(process=None)

        for process in self.filter_processes():
            return self.SwitchProcessContext(process=process)

        return "Process not found!\n"

    def render(self, renderer):
        message = self.SwitchContext()
        renderer.format(message + "\n")


class VtoPMixin(object):
    """Prints information about the virtual to physical translation."""

    name = "vtop"

    PAGE_SIZE = 0x1000

    @classmethod
    def args(cls, parser):
        super(VtoPMixin, cls).args(parser)
        parser.add_argument("virtual_address", type="SymbolAddress",
                            required=True,
                            help="The Virtual Address to examine.")

    def __init__(self, virtual_address=(), **kwargs):
        """Prints information about the virtual to physical translation.

        This is similar to windbg's !vtop extension.

        Args:
          virtual_address: The virtual address to describe.
          address_space: The address space to use (default the
            kernel_address_space).
        """
        super(VtoPMixin, self).__init__(**kwargs)
        if not isinstance(virtual_address, (tuple, list)):
            virtual_address = [virtual_address]

        self.addresses = [self.session.address_resolver.get_address_by_name(x)
                          for x in virtual_address]

    def render(self, renderer):
        if self.filtering_requested:
            with self.session.plugins.cc() as cc:
                for task in self.filter_processes():
                    cc.SwitchProcessContext(task)

                    for vaddr in self.addresses:
                        self.render_address(renderer, vaddr)

        else:
            # Use current process context.
            for vaddr in self.addresses:
                self.render_address(renderer, vaddr)

    def render_address(self, renderer, vaddr):
        renderer.section(name="{0:#08x}".format(vaddr))
        self.address_space = self.session.GetParameter("default_address_space")

        renderer.format("Virtual {0:addrpad} DTB {1:addr}\n",
                        vaddr, self.address_space.dtb)

        address = None
        for name, value, address in self.address_space.describe_vtop(vaddr):
            if address:
                # Properly format physical addresses.
                renderer.format(
                    "{0}@ {1} = {2:addr}\n",
                    name,
                    self.physical_address_space.describe(address),
                    value or 0)
            elif value:
                renderer.format("{0} {1}\n",
                                name,
                                self.physical_address_space.describe(value))
            else:
                renderer.format("{0}\n", name)

        # The below re-does all the analysis using the address space. It should
        # agree!
        physical_address = self.address_space.vtop(vaddr)
        if physical_address is None:
            renderer.format("Physical Address Invalid\n")

        elif address and address != physical_address:
            renderer.format(
                "Something went wrong ... Physical address should be %s\n",
                self.physical_address_space.describe(physical_address))


class PluginHelp(obj.Profile):
    """A profile containing all plugin help."""

    def _SetupProfileFromData(self, data):
        self.add_constants(**data["$HELP"])

    def DocsForPlugin(self, name):
        return self.get_constant(name)[1]

    def ParametersForPlugin(self, name):
        return self.get_constant(name)[0]
