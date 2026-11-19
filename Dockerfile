# Imagen base: Python 3.11 slim
FROM python:3.11-slim

# Instalar Node.js, npm, curl y git (necesarios para AWS CDK)
RUN apt-get update && apt-get install -y \
    nodejs \
    npm \
    curl \
    git \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Instalar AWS CDK y cdklocal globalmente
# - aws-cdk:        CLI de AWS CDK
# - aws-cdk-local:  wrapper que redirige cdklocal → emulador local (puerto 4566)
RUN npm install -g aws-cdk aws-cdk-local

# Directorio de trabajo para el código de la aplicación
WORKDIR /app

# Copiar e instalar dependencias Python antes de copiar el resto
# (aprovecha la caché de capas de Docker)
COPY app/requirements.txt .
# requirements.txt incluye las dependencias de la Lambda Layer con -r
COPY app/layers/dependencias/requirements.txt layers/dependencias/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Variables de entorno: credenciales dummy para el emulador
# (el emulador acepta cualquier valor; no se usan credenciales reales)
ENV AWS_ACCESS_KEY_ID=test
ENV AWS_SECRET_ACCESS_KEY=test
ENV AWS_DEFAULT_REGION=us-east-1

# El contenedor queda en bash para desarrollo interactivo
CMD ["bash"]
