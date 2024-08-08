# Utiliser une image Alpine Linux de base
FROM alpine:latest

# Mettre à jour le système et installer Python3, pip, bash et venv
RUN apk update && \
    apk add --no-cache python3 py3-pip bash python3-dev py3-virtualenv ffmpeg

# Créer un environnement virtuel et installer scdl
RUN python3 -m venv /venv && \
    . /venv/bin/activate && \
    pip install --no-cache-dir scdl

# Définir l'environnement virtuel comme l'environnement Python par défaut
ENV PATH="/venv/bin:$PATH"

# Récupère les titres déjà téléchargés
VOLUME ["/download"]

WORKDIR /download

# Définir Bash comme commande par défaut
CMD ["sh", "-c", "chmod +x /download/*.sh && /bin/bash"]

