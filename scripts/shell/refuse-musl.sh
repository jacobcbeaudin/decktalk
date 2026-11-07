# conda-forge publishes no musl builds, so nothing `pixi global install` fetches can start on musl.
# The check is that the installer says so and stops before it downloads anything, not that it fails late.
#
# Run as `sh -eu` inside the image, with install.sh mounted read only at /install.sh. It sources no
# setup, because it needs no channel and no tool beyond the curl it installs.
apk add --no-cache curl >/dev/null
set +e
out="$(sh /install.sh 2>&1)"
code=$?
set -e
printf "%s\n" "$out"
if [ "$code" -ne 1 ]; then
  echo "expected exit 1 on musl, got $code"
  exit 1
fi
case "$out" in
*musl*glibc*) ;;
*) echo "the refusal never says musl and glibc, so it teaches nothing"; exit 1 ;;
esac
if command -v pixi >/dev/null 2>&1 || [ -e "$HOME/.pixi/bin/pixi" ]; then
  echo "it installed Pixi before refusing"
  exit 1
fi
