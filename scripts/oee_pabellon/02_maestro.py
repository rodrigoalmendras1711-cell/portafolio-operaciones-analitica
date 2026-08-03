"""

CONSTRUCTOR DE MAESTRO DE PROGRAMACIÓN  |  Clínica 

Proyecto: Optimización de Capacidad Estocástica de Pabellón
Autor: Rodrigo Almendras (Ingeniería Civil Industrial)

Descripción:
Módulo de "Auto-Discovery" (Arranque en frío). Al no contar con el maestro 
oficial durante el desarrollo, este script infiere los tiempos
estándar de programación observando el comportamiento real de los cirujanos.
Detecta nuevas cirugías que no están tipificadas y auto-actualiza la base 
de datos proponiendo bloques redondeados a 5 minutos.

"""

import pandas as pd
from pathlib import Path
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

def actualizar_maestro_cirugias():
    # RUTAS DINÁMICAS (Agnósticas al entorno)
    BASE_DIR = Path(__file__).resolve().parent.parent.parent
    
    # Rutas relativas a la estructura del portafolio
    ruta_hechos  = BASE_DIR / "output" / "oee_pabellon" / "hechos_pabellon.csv"
    ruta_maestro = BASE_DIR / "scripts" / "oee_pabellon" / "Maestro_Programacion.xlsx"

    print("="*60)
    print("MAESTRO DE PROGRAMACIÓN: PROTECTOR DE ESTÁNDARES (V2.1)")
    print("="*60)

    # 1. CARGAR REALIDAD (HECHOS)
    if not ruta_hechos.exists():
        logging.error(f"No se encontró el archivo de hechos en {ruta_hechos}")
        logging.info("Ejecuta primero el script ETL (01_etl_pabellon.py) para generarlo.")
        return

    # Forzamos lectura como string para evitar que IDs se vuelvan floats
    df_hechos = pd.read_csv(ruta_hechos, dtype={'Cirujano': str, 'Intervencion_Clean': str})

    # Limpieza profunda de las columnas de la llave
    df_hechos['Cirujano'] = df_hechos['Cirujano'].fillna("DESCONOCIDO").str.strip().str.upper()
    df_hechos['Intervencion_Clean'] = df_hechos['Intervencion_Clean'].fillna("SIN_NOMBRE").str.strip().str.upper()

    # Generamos la clave única de hechos de forma consistente
    df_hechos['Clave_Unica'] = df_hechos['Cirujano'] + "|" + df_hechos['Intervencion_Clean']

    # Calculamos tiempo programado observado (Programado = Real - Delta)
    df_hechos['Obs_Prog'] = (df_hechos['T_total_real'] - df_hechos['Delta_min']).fillna(90)

    # 2. CARGAR O INICIALIZAR EL MAESTRO
    if ruta_maestro.exists():
        # Cargamos el Excel existente asegurando tipos
        df_maestro = pd.read_excel(ruta_maestro, sheet_name='Parametros_Cirugia', dtype=str)
        df_maestro['Minutos_Programados'] = pd.to_numeric(df_maestro['Minutos_Programados'], errors='coerce')
        
        # Limpiamos la clave del maestro para que el "match" sea perfecto
        df_maestro['Clave_Unica'] = df_maestro['Clave_Unica'].astype(str).str.strip().str.upper()
        logging.info(f"Maestro actual cargado con {len(df_maestro)} estándares.")
    else:
        df_maestro = pd.DataFrame(columns=['Cirujano', 'Intervencion_Clean', 'Minutos_Programados', 'Clave_Unica'])
        logging.info("No existe el maestro. Se inicializará un repositorio en blanco.")

    # 3. IDENTIFICAR COMBINACIONES NUEVAS
    claves_hechos = set(df_hechos['Clave_Unica'].unique())
    claves_maestro = set(df_maestro['Clave_Unica'].unique())

    # Filtramos posibles claves vacías o ruidosas
    claves_nuevas = (claves_hechos - claves_maestro) - {"NAN|NAN", "|", "NAN|", "|NAN"}

    if claves_nuevas:
        logging.info(f"✨ Se detectaron {len(claves_nuevas)} combinaciones médico-cirugía nuevas.")
        nuevas_filas = []
        
        for clave in claves_nuevas:
            # Filtramos hechos para esta clave específica
            match = df_hechos[df_hechos['Clave_Unica'] == clave]
            
            # Verificamos que el match NO esté vacío antes de operar
            if not match.empty:
                ultimo_dato = match.iloc[-1]
                
                # Redondeo a 5 minutos para la propuesta inicial
                valor_propuesto = int((ultimo_dato['Obs_Prog'] / 5).round() * 5)
                
                nuevas_filas.append({
                    'Cirujano': ultimo_dato['Cirujano'],
                    'Intervencion_Clean': ultimo_dato['Intervencion_Clean'],
                    'Minutos_Programados': valor_propuesto,
                    'Clave_Unica': clave
                })
        
        # 4. ACTUALIZAR EXCEL MAESTRO
        if nuevas_filas:
            # Concatenamos lo nuevo sin tocar lo que el usuario/gerencia ya editó
            df_final = pd.concat([df_maestro, pd.DataFrame(nuevas_filas)], ignore_index=True)
            
            # Guardamos actualizando la pestaña
            df_final.to_excel(ruta_maestro, index=False, sheet_name='Parametros_Cirugia')
            logging.info(f"Maestro actualizado exitosamente. Total registros: {len(df_final)}")
    else:
        logging.info("El maestro ya está al día. No se requieren cambios en la parametrización.")

    print("="*60)

if __name__ == "__main__":
    actualizar_maestro_cirugias()