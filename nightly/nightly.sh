#!/bin/bash
# Traitement de nuit : arrête MagicMirror, récupère les photos OneDrive, applique le filtre Ghibli (durée max HOURS h),
# puis relance MagicMirror (retour à la première page). MagicMirror est TOUJOURS relancé, même en cas d'erreur.
# Usage : nightly.sh [heures]   (5 par défaut)
BASE="$(cd "$(dirname "$0")" && pwd)"
HOURS="${1:-5}"
export PATH="/usr/local/bin:/usr/bin:/bin:$PATH"
mkdir -p "$BASE/logs"
LOG="$BASE/logs/$(date +%F).log"
exec >>"$LOG" 2>&1
exec 9>"$BASE/.lock"
flock -n 9 || { echo "déjà en cours"; exit 0; }
START=$(date +%s)
DEADLINE=$((START + HOURS * 3600))
echo "=== $(date -Is) début (max ${HOURS} h) ==="
restart_mm() {
	echo "$(date -Is) relance MagicMirror"
	pm2 start MagicMirror >/dev/null 2>&1 || pm2 restart MagicMirror >/dev/null 2>&1
}
trap restart_mm EXIT
pm2 stop MagicMirror >/dev/null 2>&1
sleep 5
SYNC_OK=0
node "$BASE/sync.js" && SYNC_OK=1
[ "$SYNC_OK" = 1 ] || echo "sync a échoué : pas de nettoyage, on traite seulement les originaux déjà présents"
if [ -f "$BASE/data/manifest.json" ]; then
	"$BASE/venv/bin/python" "$BASE/ghibli.py" --deadline "$DEADLINE" $([ "$SYNC_OK" = 1 ] || echo --no-cleanup)
fi
find "$BASE/logs" -name '*.log' -mtime +30 -delete
echo "=== $(date -Is) fin ($(( ($(date +%s) - START) / 60 )) min) ==="
