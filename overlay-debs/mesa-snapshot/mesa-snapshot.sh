#! /bin/sh
#
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

COMMIT=${COMMIT:-origin/main}

set -e

DEB_FILE=${DSC_FILE%%.dsc}.debian.tar.xz
ORIG_FILE=$(echo ${DSC_FILE} | sed -e 's/-.*/.orig.tar.xz/')

rm -rf mesa
git clone --depth 1 https://gitlab.freedesktop.org/mesa/mesa

cd mesa
git fetch --depth 1 origin ${COMMIT##origin/}

date=$(git log -1 --format=%cd --date=format:%Y%m%d ${COMMIT})
subject=$(git log -1 --format="%h (\"%s\")" ${COMMIT})
version=25.2.0~git${date}

rm -rf ../mesa-${version}
mkdir ../mesa-${version}
git archive --format=tar HEAD | tar x -C ../mesa-${version}

cd ../mesa-${version}

rm -rf debian
tar xJf ${DEB_FILE}

cat >> debian/changelog.tmp << EOF
mesa (${version}-0) experimental; urgency=medium

  * Build git version from ${date}, commit ${subject}

 -- Dmitry Baryshkov <dmitry.baryshkov@oss.qualcomm.com>  Wed, 11 Jun 2025 14:58:50 +0300

EOF
cat debian/changelog >> debian/changelog.tmp
mv debian/changelog.tmp debian/changelog

# orig.tar.xz generation skips all .git* files, drop them from the source dir too.
rm -rf .git*

debian/rules regen_control
debian/rules clean
debian/rules gentarball

rm -rf ../mesa

rm ${DSC_FILE} ${DEB_FILE} ${ORIG_FILE}*
