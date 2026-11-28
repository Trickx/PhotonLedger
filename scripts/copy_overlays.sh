#!/bin/sh
# Copies the annotation overlays (PixInsight SVGs) of the astroblogy gallery into the Finals folders of the
# archive, where PhotonLedger shows them over the matching final (same file name, else same pixel size).
#
#   sh scripts/copy_overlays.sh            # only shows what would be copied
#   sh scripts/copy_overlays.sh --apply    # copies (never overwrites an existing file)
#
#   SRC      folder with the SVGs   (default: astroblogy gallery overlays)
#   ARCHIVE  archive root           (default: /Volumes/Multimedia/Astro)
#
# Not copied: m92-great-hercules-cluster.svg (2828 × 1904 px - no final of M 92 or M 13 has that size),
# ngc-7380-wizard-nebula.svg (NGC 7380 is not in the archive), ngc7000-comparison.svg (no annotation).
set -eu

SRC="${SRC:-$HOME/Documents/Repositories/astroblogy/assets/images/gallery/overlay}"
ARCHIVE="${ARCHIVE:-/Volumes/Multimedia/Astro}"
APPLY=0
[ "${1:-}" = "--apply" ] && APPLY=1

# source SVG | Finals folder in the archive | name there (empty = keep the name)
LIST="
ic1396-elephants-trunk-nebula.svg|IC 1396 (Elephant's Trunk Nebula)/Finals|
m51-whirlpool-galaxy.svg|M51 (Whirlpool Galaxy)/2026-04-14/Finals|
m65-m66-ngc3628-leo-triplet.svg|M65, M66, NGC 3628 (Leo-Triplett)/2026-03-22/Finals|
m81-m82-ngc3077-bodes-galaxie.svg|M81, M82, NGC3077 (M81 Group)/2026-04-07/Finals|
m101-pinwheel-galaxy.svg|M101 (Pinwheel Galaxy)/2026-04-06/Finals|
m97-m108-owl-nebula.svg|M97, M108 (Surfboard, Owl)/2026-04-28/Finals|
ngc4631-whale-galaxy.svg|NGC 4631 (Whale Galaxy)/2026-05-22/Finals|
m31-andromeda-galaxy.svg|M 31 (Andromeda Galaxy)/2026-08-14/Finals|M31 (Andromeda Galaxy) Astrobin.svg
"
# M 31: no Finals folder yet - created; the name equals the final in Processing/master, so the overlay
# belongs to that image only (starless/stars have the same size).

[ -d "$ARCHIVE" ] || { echo "Archiv nicht gefunden: $ARCHIVE" >&2; exit 1; }
[ -d "$SRC" ] || { echo "Quelle nicht gefunden: $SRC" >&2; exit 1; }

echo "$LIST" | while IFS='|' read -r svg dir name; do
    [ -n "$svg" ] || continue
    from="$SRC/$svg"
    to="$ARCHIVE/$dir/${name:-$svg}"
    if [ ! -f "$from" ]; then echo "FEHLT    $from"; continue; fi
    if [ -e "$to" ]; then echo "VORHANDEN $to"; continue; fi
    if [ "$APPLY" = 1 ]; then
        mkdir -p "$ARCHIVE/$dir"
        cp -n "$from" "$to"
        echo "KOPIERT  $to"
    else
        echo "WÜRDE    $svg  ->  $dir/${name:-$svg}"
    fi
done
[ "$APPLY" = 1 ] || echo "Nur Vorschau. Mit --apply kopieren."
