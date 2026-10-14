# Maintainer: microwavedKing <239034853+microwavedKing@users.noreply.github.com>
pkgname=feagle
pkgver=0.1.0
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
sha256sums=('b5cd1cfd2fd690b5a0cdc6c14e54f9dfec9d7a38f65017f59a0ac07ba21b23e0')

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
  install -Dm644 NOTICE README.md CHANGELOG.md -t "$pkgdir/usr/share/doc/$pkgname/"

  install -Dm644 systemd/feagle-indexerd.service \
    "$pkgdir/usr/lib/systemd/user/feagle-indexerd.service"
  install -Dm644 systemd/feagle-queryd.service \
    "$pkgdir/usr/lib/systemd/user/feagle-queryd.service"
}
