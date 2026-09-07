#!/bin/sh
set -eu

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
package=flowdocks
version=0.6.0
output_dir="$project_root/dist"
artifact="${package}_${version}_all.deb"

for command in dpkg-deb install mktemp gzip sha256sum du cut; do
    command -v "$command" >/dev/null 2>&1 || {
        printf 'Missing build tool: %s\n' "$command" >&2
        exit 1
    }
done

umask 022
stage=$(mktemp -d "${TMPDIR:-/tmp}/flowdocks-deb.XXXXXXXX")
trap 'rm -rf -- "$stage"' EXIT
trap 'exit 1' HUP INT TERM
chmod 0755 "$stage"

install -d "$stage/DEBIAN" "$stage/usr/bin" \
    "$stage/usr/share/flowdocks/flowdocks" \
    "$stage/usr/share/applications" \
    "$stage/usr/share/icons/hicolor/scalable/apps" \
    "$stage/usr/share/man/man1" \
    "$stage/usr/share/doc/$package"
install -m 0644 "$project_root/packaging/control" "$stage/DEBIAN/control"

# Keep the payload allowlisted: never copy a worktree, environment or cache.
for file in main.py flowdocks/__init__.py flowdocks/app.py \
    flowdocks/backend.py flowdocks/panel.py flowdocks/ui.py flowdocks/x11.py; do
    install -m 0644 "$project_root/$file" "$stage/usr/share/flowdocks/$file"
done
install -m 0755 "$project_root/packaging/flowdocks" "$stage/usr/bin/flowdocks"
install -m 0644 "$project_root/packaging/flowdocks.desktop" \
    "$stage/usr/share/applications/flowdocks.desktop"
install -m 0644 "$project_root/packaging/flowdocks.svg" \
    "$stage/usr/share/icons/hicolor/scalable/apps/flowdocks.svg"
install -m 0644 "$project_root/README.md" "$stage/usr/share/doc/$package/README.md"
install -m 0644 "$project_root/packaging/copyright" "$stage/usr/share/doc/$package/copyright"
gzip -n -9 -c "$project_root/packaging/changelog" > "$stage/usr/share/doc/$package/changelog.gz"
gzip -n -9 -c "$project_root/packaging/flowdocks.1" > "$stage/usr/share/man/man1/flowdocks.1.gz"
printf 'Installed-Size: %s\n' "$(du -sk "$stage/usr" | cut -f1)" >> "$stage/DEBIAN/control"

mkdir -p "$output_dir"
dpkg-deb --root-owner-group --build "$stage" "$output_dir/$artifact"
(
    cd "$output_dir"
    sha256sum "$artifact" > "$artifact.sha256"
)
printf 'Built %s\nChecksum %s\n' "$output_dir/$artifact" "$output_dir/$artifact.sha256"
