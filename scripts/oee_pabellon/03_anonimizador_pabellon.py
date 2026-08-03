

import pandas as pd
from pathlib import Path
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

def anonimizar_datos_pabellon():
    # 1. Configuración de Rutas
    BASE_DIR = Path(__file__).resolve().parent.parent.parent
    OUTPUT_DIR = BASE_DIR / "output" / "oee_pabellon"
    MAESTRO_PATH = BASE_DIR / "scripts" / "oee_pabellon" / "Maestro_Programacion.xlsx"

    print("="*60)
    print("INICIANDO PROTOCOLO DE ENMASCARAMIENTO DE DATOS (DATA MASKING)")
    print("="*60)

    # Archivos a procesar (solo los que tienen columna Cirujano)
    archivos_csv = [
        "hechos_pabellon.csv",
        "newsvendor_optimo.csv",
        "spc_cirujano.csv",
        "simulador_cr.csv"
    ]

    cirujanos_unicos = set()
    dataframes_csv = {}

    # 2. Leer todos los CSV generados por el ETL y recopilar nombres
    for archivo in archivos_csv:
        ruta = OUTPUT_DIR / archivo
        if ruta.exists():
            df = pd.read_csv(ruta)
            dataframes_csv[archivo] = df
            if 'Cirujano' in df.columns:
                cirujanos_unicos.update(df['Cirujano'].dropna().unique())

    # Leer el Maestro de Programación y recopilar nombres
    df_maestro = None
    if MAESTRO_PATH.exists():
        df_maestro = pd.read_excel(MAESTRO_PATH, sheet_name='Parametros_Cirugia')
        if 'Cirujano' in df_maestro.columns:
            cirujanos_unicos.update(df_maestro['Cirujano'].dropna().unique())

    if not cirujanos_unicos:
        logging.warning("No se encontraron cirujanos para anonimizar. ¿Están los archivos en la carpeta correcta?")
        return

    # 3. Crear el Diccionario de Mapeo Universal
    # Ordenamos alfabéticamente para que siempre sea determinista
    mapa_cirujanos = {}
    for i, cirujano in enumerate(sorted(cirujanos_unicos), start=1):
        # Evitar anonimizar los "DESCONOCIDO" o vacíos
        if cirujano == "DESCONOCIDO" or "SIN_NOMBRE" in cirujano:
            mapa_cirujanos[cirujano] = cirujano
        else:
            mapa_cirujanos[cirujano] = f"Cirujano {i:03d}" # Genera: Cirujano 001, Cirujano 002...

    # 4. Reemplazar nombres y actualizar Clave Única en los CSV
    for archivo, df in dataframes_csv.items():
        if 'Cirujano' in df.columns:
            df['Cirujano'] = df['Cirujano'].map(mapa_cirujanos).fillna(df['Cirujano'])
            
            # Reconstruir la llave primaria para Power BI
            if 'Clave_Unica' in df.columns and 'Intervencion_Clean' in df.columns:
                df['Clave_Unica'] = df['Cirujano'].astype(str) + "|" + df['Intervencion_Clean'].astype(str)

        # Sobrescribir archivo limpio
        df.to_csv(OUTPUT_DIR / archivo, index=False)
        logging.info(f"Archivo asegurado: {archivo}")

    # 5. Reemplazar en el Maestro de Excel
    if df_maestro is not None:
        if 'Cirujano' in df_maestro.columns:
            df_maestro['Cirujano'] = df_maestro['Cirujano'].map(mapa_cirujanos).fillna(df_maestro['Cirujano'])
            
            if 'Clave_Unica' in df_maestro.columns and 'Intervencion_Clean' in df_maestro.columns:
                df_maestro['Clave_Unica'] = df_maestro['Cirujano'].astype(str) + "|" + df_maestro['Intervencion_Clean'].astype(str)
        
        df_maestro.to_excel(MAESTRO_PATH, index=False, sheet_name='Parametros_Cirugia')
        logging.info("Maestro de programación asegurado.")

    print("-"*60)
    logging.info(f" ÉXITO: {len(mapa_cirujanos)} identidades médicas fueron anonimizadas en todos los datasets.")
    print("="*60)

if __name__ == "__main__":
    anonimizar_datos_pabellon()