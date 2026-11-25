#!/bin/sh
# Render the synthesized source texts in demos/_src into PDFs (macOS cupsfilter).
# usage: sh demos/make_pdfs.sh <src.txt> <out.pdf>
set -e
tmp=$(mktemp -t forgeai).txt
fold -s -w 90 "$1" > "$tmp"
cupsfilter "$tmp" > "$2" 2>/dev/null
rm -f "$tmp"
