#!/bin/bash
# Empaqueta el proyecto (incluyendo .env) y lo envia a una VM remota via SSH.
# Corre este script DESDE TU MAQUINA LOCAL para cada VM.
#
# Uso:
#   bash scripts/send_to_vm.sh usuario@IP-VM vm2
#   bash scripts/send_to_vm.sh ubuntu@10.0.0.5 vm3
#
# Perfiles disponibles: vm1 | vm2 | vm3 | vm4

set -e

REMOTE="$1"
VM_PROFILE="$2"
REMOTE_DIR="~/e14-scraper"
TARBALL="/tmp/e14_scraper_$(date +%s).tar.gz"

# --- Validaciones ---
if [ -z "$REMOTE" ] || [ -z "$VM_PROFILE" ]; then
    echo "Uso: $0 usuario@ip [vm1|vm2|vm3|vm4]"
    exit 1
fi

case "$VM_PROFILE" in
  vm1|vm2|vm3|vm4) ;;
  *) echo "ERROR: perfil '$VM_PROFILE' no valido. Usa vm1, vm2, vm3 o vm4."; exit 1 ;;
esac

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# --- Empaquetar ---
echo "==> Empaquetando proyecto desde: $PROJECT_ROOT"
tar czf "$TARBALL" \
    --exclude='.git' \
    --exclude='.venv' \
    --exclude='data' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='*.pyo' \
    -C "$(dirname "$PROJECT_ROOT")" \
    "$(basename "$PROJECT_ROOT")"

SIZE=$(du -sh "$TARBALL" | cut -f1)
echo "    -> $TARBALL ($SIZE)"

# --- Enviar ---
echo "==> Enviando a $REMOTE:$REMOTE_DIR ..."
scp "$TARBALL" "$REMOTE:/tmp/e14_package.tar.gz"
rm "$TARBALL"

# --- Descomprimir y preparar en la VM remota ---
echo "==> Configurando proyecto en la VM remota..."
ssh "$REMOTE" bash << ENDSSH
set -e
rm -rf $REMOTE_DIR
mkdir -p $REMOTE_DIR
tar xzf /tmp/e14_package.tar.gz -C $REMOTE_DIR --strip-components=1
rm /tmp/e14_package.tar.gz
mkdir -p $REMOTE_DIR/data/downloads
chmod +x $REMOTE_DIR/scripts/install_docker.sh
echo "Proyecto listo en $REMOTE_DIR"
ls $REMOTE_DIR
ENDSSH

# --- Instrucciones finales ---
echo ""
echo "======================================================="
echo " Proyecto enviado a $REMOTE"
echo " Ahora conéctate a esa VM y ejecuta:"
echo ""
echo "   ssh $REMOTE"
echo "   cd $REMOTE_DIR"
echo ""
echo "   # Solo la primera vez (instala Docker):"
echo "   bash scripts/install_docker.sh"
echo "   # Cierra sesion SSH y vuelve a entrar, luego:"
echo ""
echo "   docker compose build         # tarda ~10-15 min (solo 1a vez)"
echo "   docker compose --profile $VM_PROFILE up -d"
echo "   docker compose --profile $VM_PROFILE logs -f"
echo "======================================================="
