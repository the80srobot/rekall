#!/usr/bin/env python2.7
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

"""This script rebuilds all the profiles in the repository.

This script parses a file of GUIDs, one per line with the pdb filename. e.g.:

4033A4DE6936470BAB02F14DCE270B772 ntkrnlmp.pdb
AED9ED72BEE246CAAC9A587B970A8E0C1 ntkrnlpa.pdb
C77DDDA381D246EDBE11A332456F9FBE1 ntkrpamp.pdb
...

We then check if we have the pdb file in the repository's src/pdb/ directory. If
not we download it.

We then parse the pdb file and store the result in the repository under:

ntoskrnl.exe/GUID/

If that file does not exist.
"""

__author__ = "Michael Cohen <scudette@google.com>"

import argparse
import json
import gzip
import pdb
import os
import time
import traceback
import multiprocessing

from rekall import config
from rekall import interactive
from rekall import plugin
from rekall import utils

session = interactive.ImportEnvironment(verbose="debug")

PARSER = argparse.ArgumentParser(
    description='Rebuild the profile repository.')

PARSER.add_argument('path_to_guids',
                    help='Path to the GUIDs file.')

PARSER.add_argument('--rebuild', default=False, action='store_true',
                    help='Rebuild all profiles.')

PARSER.add_argument('--index', default=None,
                    help='Rebuild all indexes (module name or "all").')

PARSER.add_argument('--inventory', default=False, action='store_true',
                    help='Rebuild repository inventory.')

PARSER.add_argument('--generate_help', default=False, action='store_true',
                    help='Regenerate the help profile')

PARSER.add_argument('--debug', default=False, action='store_true',
                    help='Break on exception')


NUMBER_OF_CORES = multiprocessing.cpu_count()

PDB_TO_SYS = {
    "conhost.pdb": "conhost",
    "dnsrslvr.pdb": "dnsrslvr",
    "lsasrv.pdb": "lsasrv",
    "ntdll.pdb": "ntdll",
    "ntkrnlmp.pdb": "nt",
    "ntkrnlpa.pdb": "nt",
    "ntkrpamp.pdb": "nt",
    "ntoskrnl.pdb": "nt",
    "tcpip.pdb": "tcpip",
    "tcpip6.pdb": "tcpip",
    "tcpipreg.pdb": "tcpip",
    "wdigest.pdb": "wdigest",
    "win32k.pdb": "win32k",
    }

FILENAMES_TO_TRY = [
    "ntkrnlmp.pdb",
    "ntkrnlpa.pdb",
    "ntkrpamp.pdb",
    "ntoskrnl.pdb",
    "tcpip6.pdb",
    "tcpip.pdb",
    "tcpipreg.pdb",
    "win32k.pdb",
    "ntdll.pdb",
    ]


def EnsurePathExists(path):
    try:
        os.makedirs(path)
    except OSError:
        pass


def BuildProfile(pdb_filename, profile_path, metadata):
    print("Parsing %s into %s" % (pdb_filename, profile_path))
    try:
        session.RunPlugin(
            "parse_pdb",
            pdb_filename=pdb_filename,
            output=profile_path,
            metadata=metadata)

        # Gzip the output
        with gzip.GzipFile(filename=profile_path+".gz", mode="wb") as outfd:
            outfd.write(open(profile_path).read())
    except Exception:
        print("Error during profile %s" % pdb_filename)
        print("You can run it manually: "
              "rekall parse_pdb --pdb_filename=%r --output=%r --metadata=%r" %
              (pdb_filename, profile_path, metadata))
        traceback.print_exc()

    finally:
        os.unlink(profile_path)


def BuildAllProfiles(guidfile_path, rebuild=False, reindex=None):
    changed_files = set()
    new_filenames = {}
    unsuccessful = set()

    pool = multiprocessing.Pool(NUMBER_OF_CORES)
    for line in open(guidfile_path):

        line = line.strip()
        if line.startswith("#") or line == "":
            continue

        try:
            guid, pdb_filename = line.split(" ", 2)

            # We dont care about this pdb.
            if pdb_filename not in PDB_TO_SYS:
                continue

        except ValueError:
            guid, pdb_filename = line, None

        # Fetch the pdb from the MS symbol server.
        pdb_path = os.path.join("src", "pdb")
        pdb_out_filename = os.path.join(pdb_path, "%s.pdb" % guid)

        if not pdb_filename:
            for candidate_filename in FILENAMES_TO_TRY:
                try:
                    session.RunPlugin(
                        "fetch_pdb",
                        pdb_filename=candidate_filename, guid=guid,
                        dump_dir=pdb_path)
                    os.rename(os.path.join(pdb_path, candidate_filename),
                              pdb_out_filename)
                except Exception:
                    unsuccessful.add(guid)
                    continue

                pdb_filename = candidate_filename
                new_filenames[guid] = pdb_filename
                break

        if not pdb_filename:
            unsuccessful.add(guid)
            continue

        profile_path = os.path.join(PDB_TO_SYS[pdb_filename], "GUID", guid)
        # Do not export the profile if we already have it.
        if rebuild or not os.access(profile_path + ".gz", os.R_OK):
            # Dont bother downloading the pdb file if we already have it.
            if not os.access(pdb_out_filename, os.R_OK):
                session.RunPlugin(
                    "fetch_pdb",
                    pdb_filename=pdb_filename, guid=guid,
                    dump_dir=pdb_path)

                try:
                    os.rename(os.path.join(pdb_path, pdb_filename),
                              pdb_out_filename)
                except OSError:
                    unsuccessful.add(guid)
                    continue

            implementation = os.path.splitext(
                PDB_TO_SYS[pdb_filename])[0].capitalize()

            metadata = dict(
                ProfileClass=implementation,
                PDBFile=pdb_filename,
                )

            changed_files.add(PDB_TO_SYS[pdb_filename])
            pool.apply_async(
                BuildProfile,
                (pdb_out_filename, profile_path, metadata))

        if reindex == PDB_TO_SYS[pdb_filename] or reindex == "all":
            changed_files.add(PDB_TO_SYS[pdb_filename])

    # Wait here until all the pool workers are done.
    pool.close()
    pool.join()

    if new_filenames:
        print("Found %d new file names:" % len(new_filenames))
        for guid, filename in sorted(new_filenames.items()):
            print("%s %s" % (guid, filename))

    if unsuccessful:
        print("Unable to download pdbs for:")
        for guid in sorted(unsuccessful):
            print(guid)

    return changed_files


def RebuildHelp():
    """Rebuilds the plugin help profile."""
    help_dict = {}
    plugin_metadata = {}
    result = {
        "$METADATA": dict(
            Type="Profile",
            ProfileClass="PluginHelp"
            ),
        "$HELP": help_dict,
        "$PLUGINS": plugin_metadata,
        }

    for cls in plugin.Command.classes.values():
        session.report_progress("Rebuilding profile help: %s.", cls.__name__)

        # Use the info class to build docstrings for all plugins.
        info_plugin = session.plugins.info(cls)

        default_args = [
            x for x, _ in info_plugin.get_default_args()]

        doc = utils.SmartUnicode(info_plugin)
        help_dict[cls.__name__] = [default_args, doc]

        command_metadata = config.CommandMetadata(cls)
        plugin_metadata[cls.__name__] = command_metadata.Metadata()

    with gzip.GzipFile(filename="help_doc.gz", mode="wb") as outfd:
        outfd.write(json.dumps(result))


def RebuildInventory():
    old_inventory = {}
    try:
        with gzip.GzipFile(filename="inventory.gz", mode="rb") as outfd:
            old_inventory = json.load(outfd)["$INVENTORY"]
    except IOError:
        pass

    inventory = {}
    metadata = dict(Type="Inventory",
                    ProfileClass="Inventory")

    result = {
        "$METADATA": metadata,
        "$INVENTORY": inventory,
    }

    modified = False
    for root, _, files in os.walk('./'):
        for filename in files:
            if filename.endswith(".gz"):
                path = os.path.join(root, filename)
                profile_name = os.path.join(root[2:], filename[:-3])

                file_modified_time = os.stat(path).st_mtime
                try:
                    last_modified = old_inventory[profile_name]["LastModified"]

                    # If the current file is not fresher than the old file, we
                    # just copy the metadata from the old profile.
                    if file_modified_time >= last_modified:
                        inventory[profile_name] = old_inventory[profile_name]
                        continue

                except KeyError:
                    pass

                session.report_progress("Adding %s to inventory", path)
                with gzip.GzipFile(filename=path, mode="rb") as fd:
                    data = json.load(fd)

                    inventory[profile_name] = data["$METADATA"]
                    inventory[profile_name][
                        "LastModified"] = file_modified_time
                    modified = True

    if modified:
        # Update the last modified time for the inventory itself.
        result["LastModified"] = time.time()

    with gzip.GzipFile(filename="inventory.gz", mode="wb") as outfd:
        outfd.write(utils.PPrint(result))


def main():
    # Get a renderer for our own output.
    renderer = session.GetRenderer()
    renderer.start()

    if FLAGS.inventory:
        RebuildInventory()
        return

    changes = BuildAllProfiles(FLAGS.path_to_guids, rebuild=FLAGS.rebuild,
                               reindex=FLAGS.index)

    # If the files have changed, rebuild the indexes.
    for change in changes:
        print("Rebuilding profile index for %s" % change)
        output_filename = os.path.join(change, "index")
        session.RunPlugin(
            "build_index",
            output=output_filename,
            spec=os.path.join(change, "index.yaml"))

        # Gzip the output
        with gzip.GzipFile(filename=output_filename+".gz", mode="wb") as out:
            with open(output_filename, 'rb') as fd:
                out.write(fd.read())

        os.unlink(output_filename)

    if FLAGS.generate_help:
        RebuildHelp()

    RebuildInventory()


FLAGS = PARSER.parse_args()

if __name__ == "__main__":
    try:
        main()
    except Exception:
        if FLAGS.debug:
            pdb.post_mortem()
