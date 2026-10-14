# Maintainer: microwavedKing <239034853+microwavedKing@users.noreply.github.com>
pkgname=feagle
pkgver=0.1.1
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
  'poppler: PDF indexing (pdftotext)'
  'llama.cpp-git: semantic search (AUR; needs a build newer than 2026-10-06)'
  'llama.cpp-vulkan-git: semantic search with Vulkan offload (AUR)'
  'llama.cpp-cuda-git: semantic search with CUDA offload (AUR)'
  'tesseract: OCR for scanned PDFs'
  'tesseract-data-eng: English OCR data'
  'tesseract-data-rus: Russian OCR data'
  'fuzzel: launcher for "feagle pick"'
  'wofi: launcher for "feagle pick"'
  'rofi: launcher for "feagle pick" (Wayland support included since 2.0)'
  'tofi: launcher for "feagle pick" (AUR)'
  'walker: launcher for "feagle pick" (AUR)'
)
install="$pkgname.install"
source=("$pkgname-$pkgver.tar.gz::$url/archive/refs/tags/v$pkgver.tar.gz")
# NOTE: this sum refers to the tag archive above. A copy of this PKGBUILD inside
# that archive necessarily carries the previous revision's sum, so build from a
# checkout of the repository (or from the AUR) and use `makepkg --skipchecksums`
# only if you knowingly build from the archived copy.
sha256sums=('8857248df7790abd036600bf540783fc7826b4059df61f8dba8a848b76594d1a')

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

  install -Dm644 systemd/feagle-indexerd.service \
    "$pkgdir/usr/lib/systemd/user/feagle-indexerd.service"
  install -Dm644 systemd/feagle-queryd.service \
    "$pkgdir/usr/lib/systemd/user/feagle-queryd.service"
}
