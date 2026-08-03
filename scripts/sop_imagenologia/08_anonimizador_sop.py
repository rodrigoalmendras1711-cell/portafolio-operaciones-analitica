import logging
import hashlib
import os
from pathlib import Path
import pandas as pd
import numpy as np


# CONFIGURACIÓN
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def generar_hash_paciente(rut_real: str) -> str:
    """Convierte un RUT real en un ID de paciente anónimo."""
    if pd.isna(rut_real): return "PAC-UNKNOWN"
    hash_obj = hashlib.md5(str(rut_real).encode())
    return f"PAC-{hash_obj.hexdigest()[:8].upper()}"

def crear_mapa_medicos(lista_medicos: list) -> dict:
    """Crea un diccionario consistente para enmascarar nombres de médicos."""
    mapa = {}
    contador = 1
    for medico in sorted(list(set(lista_medicos))):
        if pd.isna(medico): continue
        # Asignar un seudónimo
        mapa[medico] = f"Médico {contador:02d}"
        contador += 1
    return mapa

def aplicar_ruido_financiero(valor: float) -> float:
    """Aplica ±20% de ruido aleatorio a valores financieros para proteger tarifas reales."""
    if pd.isna(valor) or valor == 0: return valor
    ruido = np.random.uniform(0.80, 1.20)
    return round(float(valor) * ruido, 0)


# ORQUESTADOR DE ANONIMIZACIÓN

def anonimizar_dominio_sop(dir_origen: Path, dir_destino: Path):
    logging.info("=== INICIANDO PROCESO DE ANONIMIZACIÓN S&OP ===")
    dir_destino.mkdir(parents=True, exist_ok=True)

    # 1. Identificar todos los médicos en todas las bases para mantener consistencia
    nombres_unicos = set()
    
    rutas_con_medicos = {
        'fact_examenes.parquet': 'Informe firmado por',
        'fact_examenes_enriquecido.parquet': 'Informe firmado por',
        'matriz_cuotas_integrada.parquet': 'doctor',
        'prob_atraso_v2.parquet': 'Doctor'
    }

    for archivo, columna in rutas_con_medicos.items():
        ruta = dir_origen / archivo
        if ruta.exists():
            df_temp = pd.read_parquet(ruta)
            nombres_unicos.update(df_temp[columna].dropna().unique())

    mapa_medicos = crear_mapa_medicos(list(nombres_unicos))
    logging.info(f"Se generaron {len(mapa_medicos)} seudónimos de médicos.")

    # 2. Procesar cada archivo .parquet
    archivos = list(dir_origen.glob("*.parquet"))
    
    for ruta in archivos:
        df = pd.read_parquet(ruta)
        modificado = False

        # A. Enmascarar Médicos
        for col in ['Informe firmado por', 'doctor', 'Doctor']:
            if col in df.columns:
                df[col] = df[col].map(mapa_medicos).fillna(df[col])
                modificado = True

        # B. Enmascarar Pacientes
        for col in ['Id paciente', 'RUT_limpio']:
            if col in df.columns:
                df[col] = df[col].apply(generar_hash_paciente)
                modificado = True

        # C. Enmascarar Finanzas
        for col in ['monto_total_mk', 'Tarifa_Promedio']:
            if col in df.columns:
                df[col] = df[col].apply(aplicar_ruido_financiero)
                modificado = True

        # Guardar en la nueva carpeta (Portfolio Data)
        ruta_salida = dir_destino / ruta.name
        
        # Sanitizar para Power BI
        for col in df.select_dtypes(include=["object", "str"]).columns:
            df[col] = df[col].astype(str).str.strip()
            
        df.to_parquet(ruta_salida, index=False)
        logging.info(f"✔ Exportado anónimo: {ruta.name}")

    logging.info(f"=== ANONIMIZACIÓN COMPLETADA ===")
    logging.info(f"Tus datos para GitHub y Power BI están en: {dir_destino.name}/")

if __name__ == "__main__":
    BASE_DIR = Path(__file__).resolve().parent.parent.parent
    
    anonimizar_dominio_sop(
        dir_origen=BASE_DIR / 'output/sop_imagenologia',
        dir_destino=BASE_DIR / 'portfolio_data/sop_imagenologia'
    )