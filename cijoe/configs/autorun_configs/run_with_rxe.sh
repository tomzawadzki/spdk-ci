#!/usr/bin/env bash
# Run a command as root with two Soft-RoCE devices, which the SPDK nvmf tests
# take for mlx5 NICs.
#
# rxe_N runs on veth srdma_N, for N = 0 and 1. test/nvmf/common.sh finds RDMA
# NICs by PCI vendor and device ID, and rxe has no PCI device. So the command
# runs in a private mount namespace, where /sys/bus/pci/devices also has a
# ConnectX-5 (15b3:1017) entry for each veth, with no driver bound.
set -euo pipefail

if [[ ${1:-} != --unshared ]]; then
	exec unshare -m --propagation private bash "$0" --unshared "$@"
fi
shift

bus=fe
if compgen -G "/sys/bus/pci/devices/0000:$bus:*" > /dev/null; then
	echo "PCI bus $bus is in use" >&2
	exit 1
fi

modprobe rdma_rxe
for i in 0 1; do
	net=srdma_$i rxe=rxe_$i
	if [[ ! -e /sys/class/net/$net ]]; then
		ip link add "$net" type veth peer name "${net}_p"
	fi
	if [[ $(ip -o -d link show "$net") != *" veth "* ]]; then
		echo "$net exists but is not a veth" >&2
		exit 1
	fi
	ip link set "$net" up
	ip link set "${net}_p" up
	if [[ ! -e /sys/class/infiniband/$rxe ]]; then
		rdma link add "$rxe" type rxe netdev "$net"
	fi
	if [[ $(< "/sys/class/infiniband/$rxe/parent") != "$net" ]]; then
		echo "$rxe exists but is not attached to $net" >&2
		exit 1
	fi
	# allocate_nic_ips skips interfaces that have an address. Flush them, so
	# the tests always get .8 and .9.
	ip addr flush dev "$net"
done

fake=$(mktemp -d)
mount -t tmpfs none "$fake"
for d in /sys/bus/pci/devices/*; do
	ln -s "$(readlink -f "$d")" "$fake/${d##*/}"
done
for i in 0 1; do
	dev=$fake/0000:$bus:00.$i
	mkdir "$dev" "$dev/net" "$dev/net/srdma_$i"
	echo 0x020000 > "$dev/class"
	echo 0x15b3 > "$dev/vendor"
	echo 0x1017 > "$dev/device"
	echo 0x15b3 > "$dev/subsystem_vendor"
	echo 0x0007 > "$dev/subsystem_device"
	# DPDK needs resource and the subsystem IDs to scan the bus, and lspci
	# reads config.
	for _ in {1..13}; do
		echo "0x0000000000000000 0x0000000000000000 0x0000000000000000"
	done > "$dev/resource"
	# 256 bytes of config space with the vendor and device ID at 0x00, the
	# class at 0x09 and the subsystem vendor and device ID at 0x2c.
	{
		printf '\xb3\x15\x17\x10\0\0\0\0\0\0\0\x02'
		head -c 32 /dev/zero
		printf '\xb3\x15\x07\x00'
		head -c 208 /dev/zero
	} > "$dev/config"
done
mount --bind "$fake" /sys/bus/pci/devices
umount "$fake"
rmdir "$fake"

exec "$@"
