#!/bin/sh

set -eux

CDT_FILENAME="$1"

disk_type="unknown"

# make a copy of partitions.conf; infer storage type from lines
# like:
#     --disk --type=ufs --size=137438953472 ...
# or:
#     --disk --type=emmc --size=76841669632 ...
# and patch/add filenames for partitions from lines like:
#     --partition --lun=4 --name=dtb_a ... --filename=dtb.bin
# or without filename like:
#     --partition --lun=3 --name=cdt --size=128KB --type-guid=...
# for data from the following table
#
# |--------|--------------|-------------------|-----------------|
# | data   | ptool name   | ptool filename    | debos filename  |
# |--------|--------------|-------------------|-----------------|
# | ESP    | efi          | efi.bin           | disk-media.img1 |
# | rootfs | rootfs       | rootfs.img        | disk-media.img2 |
# | DTBs   | dtb_a, dtb_b | dtb.bin           | dtb.bin         |
# | CDTs   | cdt          | unset / per board | from download   |
# |--------|--------------|-------------------|-----------------|

while read line; do
	case "$line" in
		# detect storage type
		"--disk "*)
			disk_type="$(echo "$line" | sed -n 's/.*--type=\([^ ]*\).*/\1/p')"
			case $disk_type in
				"emmc")
					esp="../disk-sdcard.img1"
					rootfs="../disk-sdcard.img2"
					;;
				"ufs")
					esp="../disk-ufs.img1"
					rootfs="../disk-ufs.img2"
					;;
			esac
			echo "$disk_type" >disk_type
		;;
		# read partitions
		"--partition "*)
			name="$(echo "$line" | sed -n 's/.*--name=\([^ ]*\).*/\1/p')"
			filename=""
			case "$name" in
				dtb_a|dtb_b)
					filename="dtb.bin"
					;;
				efi)
					filename="$esp"
					;;
				rootfs)
					filename="$rootfs"
					;;
				cdt)
					if [ -n "${CDT_FILENAME}" ]; then
						filename="$(basename "${CDT_FILENAME}")"
					else
						echo "cdt partition found but missing cdt_filename, skipping"
					fi
					;;
			esac
			# override/set filename
			if [ -n "$filename" ]; then
				line="$(echo "$line" | sed 's/ --filename=[^ ]*//')"
				line="${line} --filename=${filename}"
			fi
			;;
	esac
	echo "$line"
done
