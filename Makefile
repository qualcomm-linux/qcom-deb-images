SHELL := /bin/bash

.PHONY: all check test
all: check

# Check the entire database together, including conflicts across documents.
check: vex-schema osv-schema
	shopt -s nullglob globstar; scripts/vex-check.py vex/**/*.openvex.json advisories/**/*.osv.json

test: vex-schema osv-schema
	py.test-3 --verbose ci/test_vex.py

# OpenVEX v0.2.0 schema (CC0-1.0); keep validation reproducible without
# vendoring the upstream file. Update the revision and checksum together.
OPENVEX_SCHEMA_REV := a68ccd19b15a9604d28ef66ebf33f27a772ba4ec
OPENVEX_SCHEMA_SHA256 := 9373597734ed1d3ea5161a8b46d3866c4a8cfe76fd632fdd16aef01fb34b3238

.PHONY: vex-schema
vex-schema: .cache/openvex_json_schema.json

.cache/openvex_json_schema.json: Makefile
	mkdir -p $(@D)
	curl --fail --silent --show-error --location --retry 3 \
		'https://raw.githubusercontent.com/openvex/spec/$(OPENVEX_SCHEMA_REV)/openvex_json_schema.json' \
		-o $@.tmp
	echo '$(OPENVEX_SCHEMA_SHA256)  $@.tmp' | sha256sum --check
	mv $@.tmp $@


# OSV schema v1.9.2 (Apache-2.0), likewise. Update the revision and checksum
# together.
OSV_SCHEMA_REV := c8a57bdc5b0bc54646e140cabbb7834bd0b5aec9
OSV_SCHEMA_SHA256 := 15b10dbb4c4e31c7aae11d22cdd991a9a374758db7c8389e62b3cc37cac92653

.PHONY: osv-schema
osv-schema: .cache/osv_schema.json

.cache/osv_schema.json: Makefile
	mkdir -p $(@D)
	curl --fail --silent --show-error --location --retry 3 \
		'https://raw.githubusercontent.com/ossf/osv-schema/$(OSV_SCHEMA_REV)/validation/schema.json' \
		-o $@.tmp
	echo '$(OSV_SCHEMA_SHA256)  $@.tmp' | sha256sum --check
	mv $@.tmp $@
