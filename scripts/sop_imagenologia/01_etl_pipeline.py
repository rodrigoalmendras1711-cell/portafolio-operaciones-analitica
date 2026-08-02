import logging
import warnings
from pathlib import Path
from typing import Optional

import pandas as pd
import numpy as np


# CONFIGURACIÓN Y LOGGING
# Silenciar advertencias estéticas de openpyxl al leer Excels crudos
warnings.filterwarnings('ignore', category=UserWarning, module='openpyxl')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)


# PARÁMETROS DEL NEGOCIO (S&OP IMAGENOLOGÍA)

MODALIDADES_EXCLUIR = ['US']  # Ajustar según las reglas clínicas de exclusión
AE_EXCLUIR = ['PRUEBA', 'TEST'] 
MODALIDADES_COMPLEJAS = ['CT', 'MR', 'PT', 'RM']
NOMBRE_ITMS = 'ITMS'
NOMBRE_NOT_ASSIGNED = 'NOT ASSIGNED'
SLA_DIAS_HABILES = 2


# MOTOR DE REGLAS DE NEGOCIO (SLA ENGINE)

def dias_habiles_entre(inicio: pd.Timestamp, fin: pd.Timestamp) -> Optional[int]:
    """Calcula los días hábiles (lunes a viernes) entre dos fechas"""
    if pd.isnull(inicio) or pd.isnull(fin):
        return None
    try:
        d1 = inicio.date()
        d2 = fin.date()
        if d1 > d2:
            return 0
        return int(np.busday_count(d1, d2))
    except Exception as e:
        logging.debug(f"Error calculando días hábiles: {e}")
        return None

def calcular_sla_estado(inicio: pd.Timestamp, fin: pd.Timestamp) -> str:
    """Clasifica el estado del servicio según el Acuerdo de Nivel de Servicio (SLA)."""
    dias = dias_habiles_entre(inicio, fin)
    if dias is None:
        return 'Pendiente/No Leído'
    if dias <= SLA_DIAS_HABILES:
        return 'Cumplido'
    elif dias <= 5:
        return 'Demora Leve (3-5d)'
    else:
        return 'Demora Crítica (+5d)'


# PIPELINE ETL (EXTRACCIÓN, TRANSFORMACIÓN Y CARGA)

def ejecutar_etl_imagenologia(ruta_origen: Path, ruta_destino: Path):
    """Orquesta la limpieza y transformación de los datos del sistema Carestream"""
    logging.info("Iniciando ETL Pipeline de S&OP Imagenología...")

    # ── PASO 1: Extracción ──
    archivos = list(ruta_origen.rglob('*.xlsx'))
    logging.info(f"Archivos operacionales encontrados: {len(archivos)}")

    if not archivos:
        logging.warning(f"No se encontraron archivos Excel en {ruta_origen}. Verifica la ruta.")
        return

    # ── PASO 2: Unificación ──
    dfs = []
    for archivo in archivos:
        try:
            df_raw = pd.read_excel(archivo, dtype=str)
            df_raw['_archivo_origen'] = archivo.name
            dfs.append(df_raw)
        except Exception as e:
            logging.error(f"Error leyendo {archivo.name}: {e}")

    df = pd.concat(dfs, ignore_index=True)
    logging.info(f"Total de filas extraídas (Crudo): {len(df):,}")

    # ── PASO 3: Limpieza y Estandarización ──
    logging.info("Normalizando estructuras de datos")
    
    # Limpieza estricta de nombres de columnas
    df.columns = (df.columns
        .str.strip()
        .str.normalize('NFKD')
        .str.encode('ascii', errors='ignore')
        .str.decode('ascii')
        .str.replace('[^a-zA-Z0-9_ ]', '', regex=True)
        .str.strip())

    # Transformación de fechas
    df['Fecha'] = pd.to_datetime(df['Fecha'], errors='coerce')
    df['Fecha de firma final'] = pd.to_datetime(df['Fecha de firma final'], dayfirst=True, errors='coerce')

    # ── PASO 3.5: Normalización de Modalidades Compuestas (Explode) ──
    # Resolvemos el problema de granularidad: 1 fila original -> N filas de modalidades
    n_antes_explode = len(df)
    
    df['Mod'] = df['Mod'].astype(str).str.strip().str.split()
    df = df.explode('Mod').reset_index(drop=True)
    df = df[df['Mod'].notna() & (df['Mod'].str.strip() != '') & (df['Mod'] != 'nan')]
    
    n_despues_explode = len(df)
    logging.info(f"Filas generadas por explosión de modalidades: +{n_despues_explode - n_antes_explode:,}")

    # ── PASO 4: Reglas de Calidad y Filtros de Negocio ──
    logging.info("Aplicando reglas de calidad y eliminando ruido transaccional...")
    
    df = df[~df['Mod'].isin(MODALIDADES_EXCLUIR)]       
    if 'AE de origen' in df.columns:
        df = df[~df['AE de origen'].isin(AE_EXCLUIR)]         

    # Eliminación de pacientes de prueba (Data Quality)
    if 'Id paciente' in df.columns:
        df = df[~df['Id paciente'].astype(str).str.contains('Unknown|PN|PS', case=False, na=False)]
    if 'Nombre del paciente' in df.columns:
        df = df[df['Nombre del paciente'].astype(str).str.strip() != '0']

    df['Estado_Limpio'] = df['Estado'].astype(str).str.strip().str.title()

    # Deduplicación de la llave de negocio (Evitar doble conteo por clics repetidos en el software)
    if 'Hora' in df.columns and 'Id paciente' in df.columns:
        df.drop_duplicates(
            subset=['Fecha', 'Hora', 'Id paciente'],
            keep='first',
            inplace=True
        )

    # ── PASO 5: Cálculo de Tiempos de Ciclo y Categorización ──
    logging.info("Calculando métricas de Tiempos de Ciclo y SLA...")

    df['Clasificacion_Mod'] = df['Mod'].apply(lambda m: 'COMPLEJO' if m in MODALIDADES_COMPLEJAS else 'SIMPLE')

    df['Tipo_Firmante'] = df['Informe firmado por'].apply(
        lambda f: 'ITMS' if f == NOMBRE_ITMS else ('SIN_ASIGNAR' if pd.isna(f) or f == NOMBRE_NOT_ASSIGNED else 'INTERNO')
    )

    df['Lead_Time_Habiles'] = df.apply(
        lambda row: dias_habiles_entre(row['Fecha'], row['Fecha de firma final']) 
        if pd.notnull(row['Fecha de firma final']) else None, 
        axis=1
    )

    df['Estado_SLA'] = df.apply(
        lambda row: 'Pendiente/No Leído' if pd.isnull(row['Fecha de firma final']) 
        else calcular_sla_estado(row['Fecha'], row['Fecha de firma final']), 
        axis=1
    )

    if 'Descripcion' in df.columns:
        df['Es_Urgencia'] = df['Descripcion'].str.contains('URG', na=False, case=False)
    
    df['Anio_Mes'] = pd.to_datetime(df['Fecha']).dt.strftime('%Y-%m')

    # ── PASO 6: Carga al Datalake Local (Zona Output) ──
    ruta_destino.mkdir(parents=True, exist_ok=True)
    ruta_parquet = ruta_destino / 'fact_examenes.parquet'
    
    df.to_parquet(ruta_parquet, index=False, compression='snappy')
    
    size_mb = ruta_parquet.stat().st_size / (1024 * 1024)
    logging.info(f"EXITO: Base consolidada guardada en {ruta_parquet}")
    logging.info(f"Tamaño final: {size_mb:.1f} MB | Filas procesadas: {len(df):,}")

if __name__ == "__main__":
    # Navegamos hasta la raíz del proyecto (portafolio-operaciones-analitica)
    BASE_DIR = Path(__file__).resolve().parent.parent.parent
    
    # Apuntamos a los dominios específicos de Imagenología
    ejecutar_etl_imagenologia(
        ruta_origen=BASE_DIR / 'data/sop_imagenologia/raw/carestream', # Ruta exacta
        ruta_destino=BASE_DIR / 'output/sop_imagenologia'
    )