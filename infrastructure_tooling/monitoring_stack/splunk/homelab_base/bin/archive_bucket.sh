#!/bin/sh
# coldToFrozenScript: splunkd runs this with the bucket directory as $1.
# Exit 0 only once the bucket is safely archived. Any other exit makes splunkd
# keep the bucket and retry later, so an absent NFS share never loses data.
set -eu

BUCKET="$1"
ARCHIVE=/opt/splunk/archive
MARKER="$ARCHIVE/.splunk-archive"

# The marker exists only on the Unraid share. Without it we are looking at the
# empty local mount point (share not mounted), so refuse rather than fill /.
if [ ! -f "$MARKER" ]; then
    echo "archive_bucket: $MARKER missing (NFS share not mounted?), keeping $BUCKET" >&2
    exit 1
fi

# $SPLUNK_DB/<index>/colddb/<bucket>
INDEX=$(basename "$(dirname "$(dirname "$BUCKET")")")
NAME=$(basename "$BUCKET")
DEST="$ARCHIVE/frozen/$INDEX"

# A completed archive is renamed into place, so an existing one is a retry.
if [ -d "$DEST/$NAME" ]; then
    exit 0
fi

mkdir -p "$DEST"
rm -rf "$DEST/$NAME.tmp"
mkdir "$DEST/$NAME.tmp"
# rawdata is all that is needed to thaw (tsidx files are rebuilt), as with coldToFrozenDir.
cp -R "$BUCKET/rawdata" "$DEST/$NAME.tmp/"
mv "$DEST/$NAME.tmp" "$DEST/$NAME"
echo "archive_bucket: archived $BUCKET to $DEST/$NAME" >&2
