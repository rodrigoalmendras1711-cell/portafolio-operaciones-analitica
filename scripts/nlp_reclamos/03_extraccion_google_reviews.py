import logging
import os
import time
from pathlib import Path
from typing import List, Dict, Any, Optional

import pandas as pd
from dotenv import load_dotenv
from outscraper import ApiClient


# CONFIGURACIÓN Y LOGGING

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# Cargar variables de entorno
load_dotenv()


# FUNCIONES DE EXTRACCIÓN Y PROCESAMIENTO

def obtener_resenas_google(query: str, limite: int) -> Optional[List[Dict[str, Any]]]:
    """Extrae reseñas de Google Maps"""
    
    # 1. Recuperar la API
    api_key = os.getenv('OUTSCRAPER_API_KEY')
    if not api_key:
        logging.error("No se encontró OUTSCRAPER_API_KEY en el archivo .env.")
        return None

    api = ApiClient(api_key=api_key)
    logging.info(f"Conectando a Outscraper API para: '{query}' (Límite: {limite})")
    
    try:
        resultados = api.google_maps_reviews(query, reviews_limit=limite, language='es')
        if resultados and len(resultados) > 0:
            return resultados[0].get('reviews_data', [])
        else:
            logging.warning("La API se conectó pero no devolvió resultados")
            return []
    except Exception as e:
        logging.error(f"Falla de red o límite excedido en Outscraper API: {e}")
        return None

def procesar_resenas(resenas_raw: List[Dict[str, Any]]) -> pd.DataFrame:
    """Limpia, tipifica y estructura los datos JSON"""
    datos_limpios = []
    
    for r in resenas_raw:
        datos_limpios.append({
            'fecha_opinion': r.get('review_datetime_utc'),
            'nota_google': r.get('review_rating'),
            'texto_opinion': r.get('review_text', 'Sin comentarios') 
        })
        
    df = pd.DataFrame(datos_limpios)
    
    if not df.empty:
        # Casteo evitar errores en Power BI
        df['fecha_opinion'] = pd.to_datetime(df['fecha_opinion']).dt.date
        df['nota_google'] = pd.to_numeric(df['nota_google'], errors='coerce')
        
    return df


# ORQUESTADOR

def ejecutar_extraccion(ruta_salida: Path):
    """Ejecuta el pipeline de extracción"""
    start_time = time.time()
    
    # Parámetros de búsqueda
    QUERY = 'Clínica del Sur ACHS Salud, Concepción, Chile'
    LIMITE = 500
    
    # 1. Extracción
    resenas_raw = obtener_resenas_google(query=QUERY, limite=LIMITE)
    
    if resenas_raw is not None:
        # 2. Procesamiento
        df = procesar_resenas(resenas_raw)
        
        if not df.empty:
            # 3. Carga a Datalake Local (Parquet)
            ruta_salida.parent.mkdir(parents=True, exist_ok=True)
            df.to_parquet(ruta_salida, index=False)
            
            
            logging.info(f"Éxito: Se extrajeron y modelaron {len(df)} reseñas externas.")
            logging.info(f"Tiempo de ejecución: {time.time() - start_time:.2f} segundos.")
            logging.info(f"Archivo disponibilizado en: {ruta_salida}")
        else:
            logging.warning("No se pudieron estructurar las reseñas extraídas.")
    else:
        logging.error("El pipeline de extracción se detuvo por un error")

if __name__ == "__main__":
    # Manejo dinámico 
    BASE_DIR = Path(__file__).resolve().parent.parent.parent
    RUTA_DESTINO = BASE_DIR / "output/google_reviews.parquet"
    
    ejecutar_extraccion(ruta_salida=RUTA_DESTINO)