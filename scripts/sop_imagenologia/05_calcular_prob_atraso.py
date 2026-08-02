import logging
import datetime
from pathlib import Path
from typing import Dict, Tuple

import pandas as pd
import numpy as np


# CONFIGURACIÓN Y PARÁMETROS DEL MODELO
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# Parámetros Estadísticos y de Decaimiento
ALPHA_DECAIMIENTO = 0.85
N_MINIMO_CONFIABLE = 20
N_MINIMO_VISIBLE = 5

# Matriz de Riesgo Clínico
UMBRALES_RIESGO = {
    "BAJO":    (0.00, 0.20),
    "MEDIO":   (0.20, 0.40),
    "ALTO":    (0.40, 0.65),
    "CRÍTICO": (0.65, 1.01),
}


# TRANSFORMACIONES Y LÓGICA DE NEGOCIO

def categorizar_complejidad_clinica(row: pd.Series) -> str:
    """Clasifica la complejidad estructural del examen según modalidad y prestaciones MK."""
    mod = str(row['Mod']).strip().upper()
    n = pd.to_numeric(row['n_prestaciones'], errors='coerce')
    n = 1 if pd.isna(n) else n
    
    mods_pesadas = ['CT', 'MR', 'PT', 'NM'] 
    
    if mod in mods_pesadas:
        if n == 1: return "Compleja L1 (1 prest)"
        if n == 2: return "Compleja L2 (2 prest)"
        return "Compleja L3 (3+ prest)"
    else:
        if n <= 2: return "Básica L1 (1-2 prest)"
        return "Básica L2 (3+ prest)"

def peso_temporal(anio_mes_str: str, hoy: datetime.date) -> float:
    """Aplica decaimiento exponencial para priorizar el comportamiento reciente del médico."""
    if pd.isna(anio_mes_str) or str(anio_mes_str) == 'nan':
        return 0.0
    try:
        anio, mes = int(str(anio_mes_str)[:4]), int(str(anio_mes_str)[5:7])
        meses_antiguedad = (hoy.year - anio) * 12 + (hoy.month - mes)
        return float(ALPHA_DECAIMIENTO ** max(meses_antiguedad, 0))
    except (ValueError, TypeError):
        return 0.0

def calcular_probabilidades_jerarquicas(df_res: pd.DataFrame) -> pd.DataFrame:
    """Calcula probabilidades ponderadas en 3 niveles de granularidad con fallback."""
    def calc_prob(grp):
        sp = grp['peso'].sum()
        if sp == 0: return pd.Series({'prob': np.nan, 'n': 0})
        return pd.Series({'prob': grp['atraso_pond'].sum() / sp, 'n': len(grp)})

    # Nivel 1: Máximo detalle (Doctor + Modalidad + Complejidad)
    g1 = df_res.groupby(['Informe firmado por', 'Mod', 'Complejidad']).apply(calc_prob).reset_index()
    g1.rename(columns={'Informe firmado por': 'Doctor', 'Mod': 'Modalidad'}, inplace=True)

    # Nivel 2: Fallback de Modalidad (Doctor + Modalidad)
    g2 = df_res.groupby(['Informe firmado por', 'Mod']).apply(calc_prob).reset_index()
    g2.rename(columns={'Informe firmado por': 'Doctor', 'Mod': 'Modalidad', 'prob': 'prob_mod', 'n': 'n_mod'}, inplace=True)

    # Nivel 3: Fallback Global (Solo Doctor)
    g3 = df_res.groupby('Informe firmado por').apply(calc_prob).reset_index()
    g3.rename(columns={'Informe firmado por': 'Doctor', 'prob': 'prob_global', 'n': 'n_global'}, inplace=True)

    df_prob = g1[g1['n'] >= N_MINIMO_VISIBLE].merge(
        g2[['Doctor', 'Modalidad', 'prob_mod', 'n_mod']], on=['Doctor', 'Modalidad'], how='left'
    ).merge(
        g3[['Doctor', 'prob_global', 'n_global']], on='Doctor', how='left'
    )

    def elegir_prob(row):
        if row['n'] >= N_MINIMO_CONFIABLE:
            return row['prob'], 'Doctor+Mod+Complejidad', 'ALTA'
        elif pd.notna(row.get('prob_mod')) and row.get('n_mod', 0) >= N_MINIMO_CONFIABLE:
            return row['prob_mod'], 'Doctor+Mod', 'MEDIA'
        elif pd.notna(row.get('prob_global')) and row.get('n_global', 0) >= N_MINIMO_CONFIABLE:
            return row['prob_global'], 'Doctor', 'BAJA'
        else:
            vals = [v for v in [row['prob'], row.get('prob_mod'), row.get('prob_global')] if pd.notna(v)]
            return (np.mean(vals) if vals else np.nan), 'Insuficiente', 'MUY BAJA'

    res = df_prob.apply(elegir_prob, axis=1, result_type='expand')
    df_prob['Prob_Atraso'], df_prob['Fuente_Calculo'], df_prob['Confianza'] = res[0], res[1], res[2]
    
    return df_prob


# ORQUESTADOR DEL MOTOR DE RIESGO

def orquestar_motor_riesgo(dir_datos: Path, dir_salidas: Path):
    logging.info(" INICIANDO MOTOR DE RIESGO DE ATRASOS (V2)")

    # 1. Extracción (Prefiere base enriquecida con MasterKey)
    ruta_enriquecido = dir_salidas / 'fact_examenes_enriquecido.parquet'
    ruta_base = dir_salidas / 'fact_examenes.parquet'

    if ruta_enriquecido.exists():
        logging.info("Consumiendo Datalake Financiero (fact_examenes_enriquecido.parquet)...")
        df = pd.read_parquet(ruta_enriquecido)
    elif ruta_base.exists():
        logging.warning("Sin datos MK. Utilizando Datalake Operacional puro (fact_examenes.parquet)...")
        df = pd.read_parquet(ruta_base)
        df['n_prestaciones'] = 1
    else:
        logging.error("No se encontraron bases de exámenes. Abortando.")
        return

    # 2. Transformación de Complejidad
    df['n_prestaciones'] = df.get('n_prestaciones', 1)
    df['Complejidad'] = df.apply(categorizar_complejidad_clinica, axis=1)

    # 3. Filtrado Clínico y Master Data Management (MDM) de Médicos
    df_res = df[
        (df["Tipo_Firmante"] == "INTERNO") &
        (df["Estado_SLA"].isin(["Cumplido", "Demora Leve (3-5d)", "Demora Crítica (+5d)"]))
    ].copy()

    ruta_doctores = dir_datos / 'externos/doctores_activos.xlsx'
    if ruta_doctores.exists():
        df_activos = pd.read_excel(ruta_doctores)
        df_activos['Nombre_Oficial'] = df_activos['Nombre_Oficial'].fillna(df_activos['Usuario_Sistema'])
        mapa_doctores = dict(zip(df_activos['Usuario_Sistema'], df_activos['Nombre_Oficial']))
        
        df_res = df_res[df_res['Informe firmado por'].isin(mapa_doctores.keys())].copy()
        df_res['Informe firmado por'] = df_res['Informe firmado por'].map(mapa_doctores)
        logging.info(f"MDM Aplicado: Catálogo de {len(df_activos)} médicos activos cruzado con éxito.")
    else:
        logging.warning("Catálogo de médicos inubicable. Entrenando modelo con usuarios crudos del sistema.")

    if df_res.empty:
        logging.error("No hay registros válidos tras aplicar filtros de Master Data.")
        return

    # 4. Cálculo Ponderado en el Tiempo
    hoy = datetime.date.today()
    df_res['peso'] = df_res['Anio_Mes'].apply(lambda x: peso_temporal(x, hoy))
    df_res['es_atraso'] = df_res['Estado_SLA'].isin(["Demora Leve (3-5d)", "Demora Crítica (+5d)"]).astype(float)
    df_res['atraso_pond'] = df_res['es_atraso'] * df_res['peso']

    # 5. Generación de Probabilidades
    df_prob = calcular_probabilidades_jerarquicas(df_res)

    # 6. Clasificación de Riesgo y Semáforos
    def nivel_riesgo(p):
        if pd.isna(p): return 'SIN_DATOS'
        for nivel, (lo, hi) in UMBRALES_RIESGO.items():
            if lo <= p < hi: return nivel
        return 'CRÍTICO'

    df_prob['Nivel_Riesgo'] = df_prob['Prob_Atraso'].apply(nivel_riesgo)
    df_prob['Prob_Pct'] = (df_prob['Prob_Atraso'] * 100).round(1)

    semaforo = {"BAJO": "🟢", "MEDIO": "🟡", "ALTO": "🟠", "CRÍTICO": "🔴"}
    df_prob['Etiqueta_PBI'] = df_prob.apply(
        lambda r: f"{semaforo.get(r['Nivel_Riesgo'],'⚪')} {r['Prob_Pct']:.0f}% ({r['Confianza']})"
        if pd.notna(r['Prob_Atraso']) else "Sin datos suficientes", axis=1
    )

    # 7. Carga (Exportación a Datamart)
    cols_out = ['Doctor', 'Modalidad', 'Complejidad', 'Prob_Atraso', 'Prob_Pct',
                'Nivel_Riesgo', 'Confianza', 'Fuente_Calculo', 'Etiqueta_PBI', 'n']
    
    df_final = df_prob[cols_out].sort_values('Prob_Atraso', ascending=False).reset_index(drop=True)
    
    for col in df_final.select_dtypes(include=['object']).columns:
        df_final[col] = df_final[col].astype(str).str.strip().replace({'nan': None})

    ruta_out_pq = dir_salidas / 'prob_atraso_v2.parquet'
    ruta_out_xl = dir_salidas / 'Prob_Atraso_V2.xlsx'

    df_final.to_parquet(ruta_out_pq, index=False)

    with pd.ExcelWriter(ruta_out_xl, engine='openpyxl') as writer:
        df_final.to_excel(writer, sheet_name='Prob_Detallada', index=False)
        pivot = df_final.groupby(['Doctor', 'Modalidad'])['Prob_Pct'].mean().round(1).unstack(fill_value=0)
        pivot.to_excel(writer, sheet_name='Matriz_Pivot')
        df_final[df_final['Nivel_Riesgo'] == 'CRÍTICO'].to_excel(writer, sheet_name='Solo_Criticos', index=False)

    # 8. Reporte Ejecutivo
    logging.info("REPORTE EJECUTIVO DE RIESGOS")
    dist = df_final['Nivel_Riesgo'].value_counts()
    for nivel in ['BAJO', 'MEDIO', 'ALTO', 'CRÍTICO', 'SIN_DATOS']:
        logging.info(f"Nivel {nivel:<10}: {dist.get(nivel, 0):>4} perfiles médicos")
    
    logging.info(f"Modelo completado exitosamente. Parquet disponible en {ruta_out_pq.name}")

if __name__ == "__main__":
    BASE_DIR = Path(__file__).resolve().parent.parent.parent
    
    orquestar_motor_riesgo(
        dir_datos=BASE_DIR / 'data/sop_imagenologia',
        dir_salidas=BASE_DIR / 'output/sop_imagenologia'
    )