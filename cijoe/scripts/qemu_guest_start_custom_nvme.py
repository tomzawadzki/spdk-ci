#!/usr/bin/env python3
"""
Start a qemu-guest with NVMe devices
====================================

This creates a configuration, which on a recent Linux installation would be
something like:

* /dev/ng0n1 -- nvm
* /dev/ng0n2 -- zns

* /dev/ng1n1 -- nvm

* /dev/ng2n1 -- nvm (mdts=0 / unlimited)
* /dev/ng3n1 -- fdp-enabled subsystem and nvm namespace

Using the the 'ng' device-handle, as it is always available, whereas 'nvme' are
only for block-devices. Regardless, the above is just to illustrate one
possible "appearance" of the devices in Linux.

Retargetable: false
-------------------
"""
import errno
import logging as log
from argparse import ArgumentParser
from pathlib import Path

from cijoe.qemu.wrapper import Guest


def add_args(parser: ArgumentParser):
    parser.add_argument("--guest_name", type=str, default=None)
    parser.add_argument("--nvme_img_root", type=str, default=None)
    parser.add_argument("--nvme_setup", type=str, default=None)


class QemuNvme:
    @staticmethod
    def generate_subsystem(id, nqn=None, aux={}):
        """
        Generate a subsystem configuration
        @param id Identifier, could be something like 'subsys0'
        @param nqn Non-qualified-name, assigned verbatim when provided
        @param aux Auxilary arguments, e.g. add {fdp: on} here, to enable fdp
        """

        args = {"id": id}
        if nqn:
            args["nqn"] = nqn
        args.update(aux)

        return [
            "-device",
            ",".join(["nvme-subsys"] + [f"{k}={v}" for k, v in args.items()]),
        ]


    @staticmethod
    def generate_controller(id, serial, mdts, downstream_bus, upstream_bus, controller_slot, subsystem=None):
        args = {
            "id": id,
            "serial": serial,
            "bus": downstream_bus,
            "mdts": mdts,
            "ioeventfd": "on",
        }
        if subsystem:
            args["subsys"] = subsystem

        return [
            "-device",
            f"xio3130-downstream,id={downstream_bus},"
            f"bus={upstream_bus},chassis=2,slot={controller_slot}",
            "-device",
            ",".join(["nvme"] + [f"{k}={v}" for k, v in args.items()]),
        ]


    @staticmethod
    def generate_namespace(controller_id, nsid, lbads, nvme_img_root, aux={}):
        """Returns qemu-arguments for a namespace configuration"""

        drive_id = f"{controller_id}n{nsid}"
        drive = {
            "id": drive_id,
            "file": str(nvme_img_root / f"{drive_id}.img"),
            "format": "raw",
            "if": "none",
            "discard": "on",
            "detect-zeroes": "unmap",
        }

        controller_namespace = {
            "id": drive_id,
            "drive": drive_id,
            "bus": controller_id,
            "nsid": nsid,
            "logical_block_size": 1 << lbads,
            "physical_block_size": 1 << lbads,
            **aux,
        }

        return drive, [
            "-drive",
            ",".join(f"{k}={v}" for k, v in drive.items()),
            "-device",
            ",".join(
                ["nvme-ns"] + [f"{k}={v}" for k, v in controller_namespace.items()]
            ),
        ]


def qemu_nvme_args(nvme_img_root):
    """
    Returns list of drive-args, a string of qemu-arguments and drive size for each namespace

    @returns drives, args, drive_size
    """

    lbads = 12
    drive_size = "8G"

    drives = []

    # NVMe configuration arguments
    nvme = []
    nvme += ["-device", "pcie-root-port,id=pcie_root_port1,chassis=1,slot=1"]

    upstream_bus = "pcie_upstream_port1"
    nvme += ["-device", f"x3130-upstream,id={upstream_bus},bus=pcie_root_port1"]

    #
    # Nvme0 - Controller for functional verification of namespaces with NVM and ZNS
    # command-sets
    #
    controller_id1 = "nvme0"
    controller_bus1 = "pcie_downstream_port1"
    controller_slot1 = 1
    nvme += QemuNvme.generate_controller(
        controller_id1, "deadbeef", 7, controller_bus1, upstream_bus, controller_slot1
    )

    # Nvme0n1 - NVM namespace
    drive1, qemu_nvme_dev1 = QemuNvme.generate_namespace(controller_id1, 1, lbads, nvme_img_root)
    nvme += qemu_nvme_dev1
    drives.append(drive1)

    # Nvme0n2 - ZNS namespace
    zoned_attributes = {
        "zoned": "on",
        "zoned.zone_size": "32M",
        "zoned.zone_capacity": "28M",
        "zoned.max_active": 256,
        "zoned.max_open": 256,
        "zoned.zrwas": 32 << lbads,
        "zoned.zrwafg": 16 << lbads,
        "zoned.numzrwa": 256,
    }

    drive2, qemu_nvme_dev2 = QemuNvme.generate_namespace(controller_id1, 2, lbads, nvme_img_root, zoned_attributes)
    nvme += qemu_nvme_dev2
    drives.append(drive2)

    # Nvme1 - Controller dedicated to Fabrics testing
    controller_id2 = "nvme1"
    controller_bus2 = "pcie_downstream_port2"
    controller_slot2 = 2
    nvme += QemuNvme.generate_controller(
        controller_id2, "adcdbeef", 7, controller_bus2, upstream_bus, controller_slot2
    )

    # Nvme1n1 - Namespace with NVM command-set
    drive3, qemu_nvme_dev3 = QemuNvme.generate_namespace(controller_id2, 1, lbads, nvme_img_root)
    nvme += qemu_nvme_dev3
    drives.append(drive3)

    # Nvme2 - Controller dedicated to testing HUGEPAGES / Large MDTS
    controller_id3 = "nvme2"
    controller_bus3 = "pcie_downstream_port3"
    controller_slot3 = 3
    nvme += QemuNvme.generate_controller(
        controller_id3, "beefcace", 0, controller_bus3, upstream_bus, controller_slot3
    )

    # Nvme2n1 - NVM namespace
    drive4, qemu_nvme_dev4 = QemuNvme.generate_namespace(controller_id3, 1, lbads, nvme_img_root)
    nvme += qemu_nvme_dev4
    drives.append(drive4)

    #
    # Nvme4 - Controller with PI enabled
    #

    controller_id5 = "nvme4"
    controller_bus5 = "pcie_downstream_port5"
    controller_slot5 = 5
    nvme += QemuNvme.generate_controller(
        controller_id5, "feebdaed", 7, controller_bus5, upstream_bus, controller_slot5
    )

    # Nvme4n1 - NVM namespace with PI type 1
    drv_pi1, qemu_nvme_dev_pi1 = QemuNvme.generate_namespace(controller_id5, 1, lbads, nvme_img_root, {"ms": 8, "pi": 1})
    nvme += qemu_nvme_dev_pi1
    drives.append(drv_pi1)

    # Nvme4n2 - NVM namespace with PI type 2
    drv_pi2, qemu_nvme_dev_pi2 = QemuNvme.generate_namespace(controller_id5, 2, lbads, nvme_img_root, {"ms": 8, "pi": 2})
    nvme += qemu_nvme_dev_pi2
    drives.append(drv_pi2)

    # Nvme4n3 - NVM namespace with PI type 3
    drv_pi3, qemu_nvme_dev_pi3 = QemuNvme.generate_namespace(controller_id5, 3, lbads, nvme_img_root, {"ms": 8, "pi": 3})
    nvme += qemu_nvme_dev_pi3
    drives.append(drv_pi3)

    return drives, nvme, drive_size


def qemu_ftl_nvme_args(nvme_img_root):
    """
    Returns list of drive-args, a string of qemu-arguments and drive size for each namespace

    @returns drives, args, drive_size
    """

    lbads = 12
    drive_size = "20G"

    drives = []
    nvme = []
    nvme += ["-device", "pcie-root-port,id=pcie_root_port1,chassis=1,slot=1"]

    upstream_bus = "pcie_upstream_port1"
    nvme += ["-device", f"x3130-upstream,id={upstream_bus},bus=pcie_root_port1"]

    #
    # Nvme0 - Base device for FTL
    #
    controller_id1 = "nvme0"
    controller_bus1 = "pcie_downstream_port1"
    controller_slot1 = 1
    nvme += QemuNvme.generate_controller(
        controller_id1, "deadbeef", 0, controller_bus1, upstream_bus, controller_slot1
    )

    # Nvme0n1 - NVM namespace
    drive1, qemu_nvme_dev1 = QemuNvme.generate_namespace(controller_id1, 1, lbads, nvme_img_root)
    nvme += qemu_nvme_dev1
    drives.append(drive1)

    #
    # Nvme1 - Cache device for FTL
    #
    controller_id2 = "nvme1"
    controller_bus2 = "pcie_downstream_port2"
    controller_slot2 = 2
    nvme += QemuNvme.generate_controller(
        controller_id2, "deadbeee", 0, controller_bus2, upstream_bus, controller_slot2
    )

    # Nvme1n1 - NVM namespace
    drive2, qemu_nvme_dev2 = QemuNvme.generate_namespace(controller_id2, 1, lbads, nvme_img_root, {"ms": 64})
    nvme += qemu_nvme_dev2
    drives.append(drive2)

    return drives, nvme, drive_size


def main(args, cijoe):
    """Start a qemu guest"""

    guest_name = args.guest_name or cijoe.getconf("qemu.default_guest")
    if not guest_name:
        log.error("missing: script-arg(guest_name) or config(qemu.default_guest)")
        return 1

    guest = Guest(cijoe, cijoe.config, guest_name)

    nvme_setup = args.nvme_setup
    if not nvme_setup:
        nvme_setup = "default"

    nvme_setups = {
        "default": qemu_nvme_args,
        "ftl": qemu_ftl_nvme_args
    }

    nvme_img_root = Path(args.nvme_img_root or guest.guest_path)

    nvme_setup = nvme_setups[nvme_setup]
    drives, nvme_args, drive_size = nvme_setup(nvme_img_root)

    # Check that the backing-storage exists, create them if they do not
    for drive in drives:
        err, _ = cijoe.run_local(f"[ -f {drive['file']} ]")
        if err:
            guest.image_create(drive["file"], drive["format"], drive_size)
        err, _ = cijoe.run_local(f"[ -f {drive['file']} ]")

    err = guest.start(extra_args=nvme_args)
    if err:
        log.error(f"guest.start() : err({err})")
        return err

    started = guest.is_up()
    if not started:
        log.error("guest.is_up() : False")
        return errno.EAGAIN

    return 0
