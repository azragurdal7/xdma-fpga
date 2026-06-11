#!/bin/bash

set -u

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
base_dir="$(cd "$script_dir/.." && pwd)"
dma_from_device="$base_dir/tools/dma_from_device"

device=${1:-/dev/xdma0_c2h_3}
count=${2:-32}
sizes=${3:-"4096 32768 262144 1048576 4194304"}

timestamp="$(date +%Y%m%d_%H%M%S)"
log_dir="$base_dir/data/benchmarks/$timestamp"
mkdir -p "$log_dir"

if [ ! -x "$dma_from_device" ]; then
	echo "ERROR: dma_from_device not found or not executable: $dma_from_device"
	exit 1
fi

if [ ! -e "$device" ]; then
	echo "ERROR: device node not found: $device"
	echo "Expected CH4 C2H node is usually /dev/xdma0_c2h_3."
	echo "Run generic.py or load_driver.sh first, then retry."
	exit 1
fi

echo "CH4 C2H bottleneck test"
echo "  device : $device"
echo "  count  : $count"
echo "  sizes  : $sizes"
echo "  log dir: $log_dir"
echo

summary="$log_dir/summary.txt"
{
	echo "device,size,count,mode,return_code,bw_line"
} > "$summary"

for size in $sizes; do
	echo "============================================================"
	echo "Size: $size bytes, Count: $count"
	echo "============================================================"

	ram_log="$log_dir/ram_only_${size}.log"
	disk_log="$log_dir/disk_write_${size}.log"
	out_file="$log_dir/output_${size}.bin"

	echo
	echo "[1/2] RAM-only C2H read: DMA -> host RAM, no output file"
	"$dma_from_device" -v -d "$device" -s "$size" -c "$count" 2>&1 | tee "$ram_log"
	ram_rc=${PIPESTATUS[0]}
	ram_bw="$(grep 'BW =' "$ram_log" | tail -1 | tr ',' ' ' | tr -s ' ')"
	echo "$device,$size,$count,ram_only,$ram_rc,$ram_bw" >> "$summary"

	echo
	echo "[2/2] Disk-write C2H read: DMA -> host RAM -> output file"
	"$dma_from_device" -v -d "$device" -s "$size" -c "$count" -f "$out_file" 2>&1 | tee "$disk_log"
	disk_rc=${PIPESTATUS[0]}
	disk_bw="$(grep 'BW =' "$disk_log" | tail -1 | tr ',' ' ' | tr -s ' ')"
	echo "$device,$size,$count,disk_write,$disk_rc,$disk_bw" >> "$summary"

	echo
	echo "Result for size $size:"
	echo "  RAM-only return code : $ram_rc"
	echo "  Disk-write return code: $disk_rc"
	echo
done

echo "Done."
echo "Summary: $summary"
cat "$summary"
