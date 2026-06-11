#!/bin/bash

set -u

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
base_dir="$(cd "$script_dir/.." && pwd)"
dma_from_device="$base_dir/tools/dma_from_device"

device=${1:-/dev/xdma0_c2h_3}
count=${2:-32}
sizes=${3:-"4096 32768 262144 1048576 4194304"}

timestamp="$(date +%Y%m%d_%H%M%S)"
log_dir="$base_dir/data/benchmarks/${timestamp}_dma_disk_breakdown"
mkdir -p "$log_dir"

summary="$log_dir/summary.csv"
notes="$log_dir/test_notes.txt"

if [ ! -x "$dma_from_device" ]; then
	echo "ERROR: dma_from_device not found or not executable: $dma_from_device"
	exit 1
fi

if [ ! -e "$device" ]; then
	echo "ERROR: device node not found: $device"
	echo "Expected CH4 C2H node is usually /dev/xdma0_c2h_3."
	echo "Load the XDMA driver first, then retry."
	exit 1
fi

cat > "$notes" <<EOF
CH4 C2H DMA + disk breakdown test
=================================

Command shape
-------------
RAM-only:
  $dma_from_device -v -d $device -s SIZE -c COUNT

Disk-write:
  $dma_from_device -v -d $device -s SIZE -c COUNT -f OUTPUT_FILE

Parameters
----------
device = XDMA C2H character device. For CH4 this is normally /dev/xdma0_c2h_3.
size   = bytes requested per single DMA read transfer.
count  = how many DMA read transfers are executed.
total_bytes = size * count.

What is timed
-------------
dma_from_device measures DMA read time internally around read_to_buffer().
That means its "Average BW" is DMA -> host RAM bandwidth.

This script additionally measures wall-clock time around the whole command.
For disk-write mode, wall-clock time includes DMA read, userspace overhead,
and writing the received buffer to the output file.

Formulas
--------
avg_dma_time_ns = dma_total_time_ns / count
dma_bw_MBps = size_bytes * 1000 / avg_dma_time_ns

wall_bw_MBps = total_bytes * 1000 / wall_time_ns

disk_extra_time_ns = disk_wall_time_ns - disk_dma_total_time_ns
disk_extra_bw_MBps = total_bytes * 1000 / disk_extra_time_ns

Important note
--------------
disk_extra_bw_MBps is an approximation of the extra file-write path cost.
It is not a pure physical SSD benchmark, because Linux page cache, O_SYNC,
filesystem behavior, scheduler noise, and userspace overhead are involved.
For pure SSD benchmarking use a storage tool such as fio. For this XDMA test,
the most reliable hardware metric remains DMA -> RAM bandwidth.

Test setup
----------
device: $device
count : $count
sizes : $sizes
log_dir: $log_dir
EOF

echo "device,size,count,total_bytes,ram_rc,ram_dma_total_ns,ram_dma_avg_ns,ram_dma_bw_MBps,ram_wall_ns,ram_wall_bw_MBps,disk_rc,disk_dma_total_ns,disk_dma_avg_ns,disk_dma_bw_MBps,disk_wall_ns,disk_wall_bw_MBps,disk_extra_ns,disk_extra_bw_MBps,output_file" > "$summary"

extract_total_ns() {
	awk '/total time/ {
		for (i = 1; i <= NF; i++) {
			if ($i == "total" && $(i + 1) == "time") {
				print $(i + 2)
				exit
			}
		}
	}' "$1"
}

extract_bw() {
	awk '/Average BW/ {
		gsub(",", "", $NF)
		bw = $NF
	}
	END {
		if (bw != "")
			print bw
	}' "$1"
}

calc() {
	awk "BEGIN { if (($2) <= 0) print \"nan\"; else printf \"%.6f\", ($1) / ($2) }"
}

echo "CH4 C2H DMA + disk breakdown test"
echo "  device : $device"
echo "  count  : $count"
echo "  sizes  : $sizes"
echo "  log dir: $log_dir"
echo

for size in $sizes; do
	total_bytes=$((size * count))
	ram_log="$log_dir/ram_only_${size}.log"
	disk_log="$log_dir/disk_write_${size}.log"
	out_file="$log_dir/output_${size}.bin"

	echo "============================================================"
	echo "Size: $size bytes, Count: $count, Total: $total_bytes bytes"
	echo "============================================================"

	echo
	echo "[1/2] RAM-only: DMA -> host RAM"
	ram_start_ns="$(date +%s%N)"
	"$dma_from_device" -v -d "$device" -s "$size" -c "$count" 2>&1 | tee "$ram_log"
	ram_rc=${PIPESTATUS[0]}
	ram_end_ns="$(date +%s%N)"
	ram_wall_ns=$((ram_end_ns - ram_start_ns))

	ram_dma_total_ns="$(extract_total_ns "$ram_log")"
	ram_dma_bw="$(extract_bw "$ram_log")"
	if [ -n "$ram_dma_total_ns" ]; then
		ram_dma_avg_ns="$(calc "$ram_dma_total_ns" "$count")"
	else
		ram_dma_total_ns="0"
		ram_dma_avg_ns="nan"
		ram_dma_bw="nan"
	fi
	ram_wall_bw="$(calc "$((total_bytes * 1000))" "$ram_wall_ns")"

	echo
	echo "[2/2] Disk-write: DMA -> host RAM -> output file"
	disk_start_ns="$(date +%s%N)"
	"$dma_from_device" -v -d "$device" -s "$size" -c "$count" -f "$out_file" 2>&1 | tee "$disk_log"
	disk_rc=${PIPESTATUS[0]}
	disk_end_ns="$(date +%s%N)"
	disk_wall_ns=$((disk_end_ns - disk_start_ns))

	disk_dma_total_ns="$(extract_total_ns "$disk_log")"
	disk_dma_bw="$(extract_bw "$disk_log")"
	if [ -n "$disk_dma_total_ns" ]; then
		disk_dma_avg_ns="$(calc "$disk_dma_total_ns" "$count")"
	else
		disk_dma_total_ns="0"
		disk_dma_avg_ns="nan"
		disk_dma_bw="nan"
	fi
	disk_wall_bw="$(calc "$((total_bytes * 1000))" "$disk_wall_ns")"

	disk_extra_ns=$((disk_wall_ns - disk_dma_total_ns))
	if [ "$disk_extra_ns" -gt 0 ]; then
		disk_extra_bw="$(calc "$((total_bytes * 1000))" "$disk_extra_ns")"
	else
		disk_extra_bw="nan"
	fi

	echo "$device,$size,$count,$total_bytes,$ram_rc,$ram_dma_total_ns,$ram_dma_avg_ns,$ram_dma_bw,$ram_wall_ns,$ram_wall_bw,$disk_rc,$disk_dma_total_ns,$disk_dma_avg_ns,$disk_dma_bw,$disk_wall_ns,$disk_wall_bw,$disk_extra_ns,$disk_extra_bw,$out_file" >> "$summary"

	{
		echo
		echo "Result for size $size"
		echo "  total bytes              : $total_bytes"
		echo "  RAM-only DMA BW          : $ram_dma_bw MB/s"
		echo "  RAM-only wall BW         : $ram_wall_bw MB/s"
		echo "  Disk-write DMA BW        : $disk_dma_bw MB/s"
		echo "  Disk-write wall BW       : $disk_wall_bw MB/s"
		echo "  Disk extra approx BW     : $disk_extra_bw MB/s"
		echo "  RAM return code          : $ram_rc"
		echo "  Disk return code         : $disk_rc"
		echo "  Output file              : $out_file"
	} | tee -a "$notes"

	echo
done

echo "Done."
echo "Summary CSV: $summary"
echo "Notes TXT  : $notes"
echo
cat "$summary"
