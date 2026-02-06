import requests
import zipfile
import os
import io

# URL officielle du GTFS d'Évéole (SMTD)
GTFS_URL = "https://transport.data.gouv.fr/resources/79544/download"
DATA_DIR = "data"

def download_and_extract():
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)
    
    print("⬇️  Téléchargement des données Évéole en cours...")
    try:
        r = requests.get(GTFS_URL)
        r.raise_for_status()
        
        print("📦 Extraction des fichiers...")
        with zipfile.ZipFile(io.BytesIO(r.content)) as zip_ref:
            zip_ref.extractall(DATA_DIR)
            
        print("✅  Données prêtes dans le dossier /data !")
    except Exception as e:
        print(f"❌ Erreur : {e}")

if __name__ == "__main__":
    download_and_extract()