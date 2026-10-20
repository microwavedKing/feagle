# Maintainer: microwavedKing <239034853+microwavedKing@users.noreply.github.com>
pkgname=feagle
pkgver=0.4.0
pkgrel=1
pkgdesc="Local semantic file search for Arch Linux + Wayland, fully offline"
arch=('any')
url="https://github.com/microwavedKing/feagle"
license=('MIT')
depends=(
  'python'
  'python-watchfiles'
  'python-httpx'
  'python-snowballstemmer'
  # AUR: sqlite-vec ships as a Python package only
  'python-sqlite-vec'
)
makedepends=(
  'python-build'
  'python-installer'
  'python-hatchling'
  'python-pytest'
)
optdepends=(
  'poppler: PDF indexing (pdftotext) and PDF page previews'
  'quickshell: the recommended QML launcher popup (AUR)'
  'llama.cpp-git: semantic search (AUR; needs a build newer than 2026-10-06)'
  'llama.cpp-vulkan-git: semantic search with Vulkan offload (AUR)'
  'llama.cpp-cuda-git: semantic search with CUDA offload (AUR)'
  'tesseract: OCR for scanned PDFs and images'
  'tesseract-data-eng: English OCR data'
  'tesseract-data-rus: Russian OCR data'
  'tesseract-data-osd: orientation detection for OCR ([index].ocr_osd)'
  'ffmpeg: image and video indexing (downscales images, extracts frames)'
  'whisper.cpp: audio transcription (AUR; provides whisper-cli)'
  'fuzzel: alternative dmenu launcher for "feagle pick"'
  'wofi: alternative dmenu launcher for "feagle pick"'
  'rofi: alternative dmenu launcher for "feagle pick" (Wayland support since 2.0)'
  'tofi: alternative dmenu launcher for "feagle pick" (AUR)'
  'walker: alternative dmenu launcher for "feagle pick" (AUR)'
)
install="$pkgname.install"
source=("$pkgname-$pkgver.tar.gz::$url/archive/refs/tags/v$pkgver.tar.gz")
# NOTE: this sum refers to the tag archive above. A copy of this PKGBUILD inside
# that archive necessarily carries the previous revision's sum, so build from a
# checkout of the repository (or from the AUR) and use `makepkg --skipchecksums`
# only if you knowingly build from the archived copy.
sha256sums=('4eec4fa07dbfdb422871d109a19dd171962d97440ad61384887efe2d6345fa33')

build() {
  cd "$pkgname-$pkgver"
  python -m build --wheel --no-isolation
}

check() {
  cd "$pkgname-$pkgver"
  python -m pytest -q
}

package() {
  cd "$pkgname-$pkgver"

  python -m installer --destdir="$pkgdir" dist/*.whl

  install -Dm644 LICENSE "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
  install -Dm644 NOTICE README.md CHANGELOG.md docs/INSTALL.ru.md \
    -t "$pkgdir/usr/share/doc/$pkgname/"

  # Manual page and shell completions (bash, zsh, fish).
  install -Dm644 man/feagle.1 "$pkgdir/usr/share/man/man1/feagle.1"
  install -Dm644 completions/feagle.bash \
    "$pkgdir/usr/share/bash-completion/completions/feagle"
  install -Dm644 completions/_feagle "$pkgdir/usr/share/zsh/site-functions/_feagle"
  install -Dm644 completions/feagle.fish \
    "$pkgdir/usr/share/fish/vendor_completions.d/feagle.fish"

  install -Dm644 systemd/feagle-indexerd.service \
    "$pkgdir/usr/lib/systemd/user/feagle-indexerd.service"
  install -Dm644 systemd/feagle-queryd.service \
    "$pkgdir/usr/lib/systemd/user/feagle-queryd.service"

  # Icon (hicolor) and an application-menu entry. The entry runs `feagle pick`,
  # so it needs one of the dmenu launchers from the optional dependencies.
  install -Dm644 assets/feagle.svg \
    "$pkgdir/usr/share/icons/hicolor/scalable/apps/feagle.svg"
  install -Dm644 assets/feagle.png \
    "$pkgdir/usr/share/icons/hicolor/512x512/apps/feagle.png"
  install -Dm644 assets/feagle.desktop \
    "$pkgdir/usr/share/applications/feagle.desktop"
}
