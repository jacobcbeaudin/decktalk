# Playwright publishes no musllinux wheels, so the resolver fails on musl whatever the installer does.
# The check is that the installer says so and stops before installing anything, not that it fails late.
#
# Run as `sh -euc` inside the image, with install.sh mounted read only at /install.sh.
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
if command -v uv >/dev/null 2>&1; then
  echo "it installed uv before refusing"
  exit 1
fi
