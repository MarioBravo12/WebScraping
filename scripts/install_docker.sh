#!/bin/bash
# Instala Docker Engine en Ubuntu 22.04/24.04 limpio.
# Corre este script UNA VEZ en cada VM antes de docker compose build.
#
# Uso:
#   bash scripts/install_docker.sh

set -e

echo "==> [1/4] Actualizando paquetes..."
sudo apt-get update -y

echo "==> [2/4] Instalando Docker Engine (oficial)..."
curl -fsSL https://get.docker.com | sudo sh

echo "==> [3/4] Agregando usuario '$USER' al grupo docker..."
sudo usermod -aG docker "$USER"

echo "==> [4/4] Habilitando e iniciando Docker..."
sudo systemctl enable docker
sudo systemctl start docker

echo ""
echo "======================================================="
echo " Docker instalado correctamente."
echo " IMPORTANTE: cierra la sesion SSH y vuelve a entrar"
echo " para que el grupo 'docker' surta efecto. Luego:"
echo "   docker compose build"
echo "   docker compose --profile vmN up -d"
echo "======================================================="
