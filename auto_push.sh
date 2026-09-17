#!/bin/bash
set -e

cd "$(dirname "$0")"

git add -A

if git diff --cached --quiet; then
  echo "Değişiklik yok, push edilecek bir şey bulunamadı."
  exit 0
fi

msg="${1:-Otomatik güncelleme $(date '+%Y-%m-%d %H:%M')}"

git commit -m "$msg"
git push

echo "✅ Push tamamlandı."