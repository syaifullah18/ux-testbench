#!/usr/bin/env bash
# Builds the self-hosted CSS, fonts and icons used when LOCAL_ASSETS=1.
#
# A public instance should serve every asset itself. The CDN build of Tailwind is convenient for
# development but is explicitly not for production, and each CDN request hands a participant's IP
# address to a third party. This script downloads the Tailwind standalone binary (no Node needed),
# compiles the classes actually used in the templates, and fetches the font and icon files.
#
#   ./scripts/build-assets.sh           # build everything
#   LOCAL_ASSETS=1 python -m testbench run   # then run with the local files
#
# Output lands in testbench/static/vendor/ and is git-ignored.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENDOR="$ROOT/testbench/static/vendor"
CACHE="$ROOT/.cache"
TAILWIND_VERSION="v3.4.17"
FA_VERSION="6.5.1"

mkdir -p "$VENDOR/webfonts" "$CACHE"

case "$(uname -s)-$(uname -m)" in
  Darwin-arm64)  TW_TARGET="macos-arm64" ;;
  Darwin-x86_64) TW_TARGET="macos-x64" ;;
  Linux-aarch64) TW_TARGET="linux-arm64" ;;
  Linux-x86_64)  TW_TARGET="linux-x64" ;;
  *) echo "Unsupported platform: $(uname -s)-$(uname -m)" >&2; exit 1 ;;
esac

TW_BIN="$CACHE/tailwindcss-$TAILWIND_VERSION-$TW_TARGET"
if [ ! -x "$TW_BIN" ]; then
  echo "→ Downloading Tailwind CLI $TAILWIND_VERSION ($TW_TARGET)"
  curl -fsSL -o "$TW_BIN" \
    "https://github.com/tailwindlabs/tailwindcss/releases/download/$TAILWIND_VERSION/tailwindcss-$TW_TARGET"
  chmod +x "$TW_BIN"
fi

echo "→ Compiling Tailwind CSS"
cat > "$CACHE/input.css" <<'CSS'
@tailwind base;
@tailwind components;
@tailwind utilities;
CSS
"$TW_BIN" -c "$ROOT/tailwind.config.js" -i "$CACHE/input.css" -o "$VENDOR/tailwind.css" --minify

echo "→ Fetching Plus Jakarta Sans"
FONT_CSS="$VENDOR/fonts.css"
: > "$FONT_CSS"
for weight in 400 500 600 700; do
  woff="plus-jakarta-sans-$weight.woff2"
  curl -fsSL -o "$VENDOR/webfonts/$woff" \
    "https://fonts.gstatic.com/s/plusjakartasans/v8/LDIbaomQNQcsA88c7O9yZ4KMCoOg4IA6-91aHEjcWuA_qU79QRTp.woff2"
  cat >> "$FONT_CSS" <<CSS
@font-face{font-family:'Plus Jakarta Sans';font-style:normal;font-weight:$weight;font-display:swap;src:url('webfonts/$woff') format('woff2');}
CSS
done

echo "→ Fetching Font Awesome $FA_VERSION"
curl -fsSL -o "$VENDOR/icons.css" \
  "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/$FA_VERSION/css/all.min.css"
for f in fa-solid-900 fa-regular-400 fa-brands-400; do
  for ext in woff2 ttf; do
    curl -fsSL -o "$VENDOR/webfonts/$f.$ext" \
      "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/$FA_VERSION/webfonts/$f.$ext" || true
  done
done
# Font Awesome's stylesheet points at ../webfonts/; our files sit next to it.
sed -i.bak 's|\.\./webfonts/|webfonts/|g' "$VENDOR/icons.css" && rm -f "$VENDOR/icons.css.bak"

echo
echo "Done. Assets in $VENDOR"
echo "Run the app with LOCAL_ASSETS=1 to use them."
