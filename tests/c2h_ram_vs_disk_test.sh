#!/bin/bash

set -u

device=${1:-/dev/xdma0_c2h_3}
size=${2:-1048576}
count=${3:-32}
out_file=${4:-/tmp/xdma_c2h_disk_test.bin}

tool_path="$(dirname "$0")/../tools"
dma_from_device="$tool_path/dma_from_device"

if [ ! -x "$dma_from_device" ]; then
	echo "ERROR: dma_from_device not found or not executable: $dma_from_device"
	exit 1
fi

if [ ! -e "$device" ]; then
	echo "ERROR: device node not found: $device"
	echo "Load the XDMA driver first, then retry."
	exit 1
fi

echo "C2H RAM-only test"
echo "  device: $device"
echo "  size  : $size bytes per transfer"
echo "  count : $count"
echo
"$dma_from_device" -v -d "$device" -s "$size" -c "$count"
ram_rc=$?

echo
echo "C2H disk-write test"
echo "  output: $out_file"
echo
"$dma_from_device" -v -d "$device" -s "$size" -c "$count" -f "$out_file"
disk_rc=$?

echo
echo "Result"
echo "  RAM-only return code : $ram_rc"
echo "  Disk-write return code: $disk_rc"
echo "  Total requested bytes : $((size * count))"

if [ "$ram_rc" -ne 0 ] || [ "$disk_rc" -ne 0 ]; then
	exit 1
fi
