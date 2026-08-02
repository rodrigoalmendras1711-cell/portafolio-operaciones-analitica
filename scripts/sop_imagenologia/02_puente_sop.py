# scripts/puente_sop.py
"""
PUENTE S&OP — Conexión Carestream (Operaciones) ↔ MK (Finanzas)
"""

import pandas as pd
import numpy as np
import os
import glob
from pathlib import Path
import warnings

# Silenciar advertencias de openpyxl
warnings.filterwarnings('ignore', category=UserWarning, module='openpyxl')

print("=" * 65)
print(" PUENTE S&OP — CARESTREAM × MK")
print("=" * 65)

# RUTAS DINÁMICAS 
BASE_DIR = Path(__file__).resolve().parent.parent.parent
RUTA_RAW = BASE_DIR / 'data/sop_imagenologia/raw'
RUTA_PROCESADO = BASE_DIR / 'output/sop_imagenologia'
RUTA_OUTPUTS = BASE_DIR / 'output/sop_imagenologia'

#  PARÁMETROS 
TOLERANCIA_DIAS   = 1      # ±días para el join por fecha
N_PRESTACIONES_BINS = [0, 1, 2, 3, float('inf')]
N_PRESTACIONES_LABELS = ["1 prest.", "2 prest.", "3 prest.", "4+ prest."]
RUTA_MK = os.path.join(RUTA_RAW, 'mk')   # Carpeta donde van los Excel MK mensuales

#  1. CARGAR CARESTREAM 
ruta_cs = os.path.join(RUTA_PROCESADO, "fact_examenes.parquet")
if not os.path.exists(ruta_cs):
    print("No se encuentra fact_examenes.parquet. Corre etl_pipeline.py primero.")
    exit()

print("\nCargando Carestream...")
df_cs = pd.read_parquet(ruta_cs)

# Normalizar RUT para el join
df_cs['RUT_limpio'] = (
    df_cs['Id paciente']
    .astype(str)
    .str.replace(r'[.\s]', '', regex=True)
    .str.strip()
    .str.upper()
)
df_cs['Fecha_date'] = pd.to_datetime(df_cs['Fecha']).dt.date
print(f"   Carestream: {len(df_cs):,} exámenes")

#  2. CARGAR MK (todos los Excel de la carpeta mk/) 
print("\nCargando base MK...")

if not os.path.exists(RUTA_MK):
    print(f" No existe la carpeta {RUTA_MK}.")
    print("   Crea la carpeta 'datos/raw/mk/' y coloca ahí los Excel MK mensuales.")
    print("   El script continuará calculando con un factor de agrupación fijo de 1.358")
    df_mk = pd.DataFrame()
else:
    archivos_mk = glob.glob(os.path.join(RUTA_MK, '**', '*.xlsx'), recursive=True)
    if not archivos_mk:
        print(f"  No hay archivos Excel en {RUTA_MK}.")
        df_mk = pd.DataFrame()
    else:
        dfs_mk = []
        for a in archivos_mk:
            try:
                # Usamos openpyxl estándar para evitar problemas de dependencias
                cols_originales = pd.read_excel(a, nrows=0, engine='openpyxl').columns
                
                cols_utiles = [
                    c for c in cols_originales 
                    if any(clave in str(c).upper().replace(' ', '') for clave in 
                           ['RUT', 'FECHA', 'NOMAGRUP', 'GRUPO', 'NOMARTICULO', 'ARTICULO', 'TOTAL', 'COSTO'])
                ]
                
                df_tmp = pd.read_excel(a, dtype=str, usecols=cols_utiles, engine='openpyxl')
                df_tmp['_archivo'] = os.path.basename(a)
                dfs_mk.append(df_tmp)
                print(f"  {os.path.basename(a)}: {len(df_tmp):,} filas")
            except Exception as e:
                print(f"  Error leyendo {a}: {e}")
        df_mk = pd.concat(dfs_mk, ignore_index=True) if dfs_mk else pd.DataFrame()

#  3. PROCESAR MK 
FACTOR_FIJO = 1.358  # Febrero 2026: 7461 MK / 5490 CS

if df_mk.empty:
    print("\nTrabajando con factor fijo histórico (sin base MK cargada)")
    usar_factor_fijo = True
else:
    usar_factor_fijo = False
    print(f"\n   MK total: {len(df_mk):,} prestaciones")

    col_map = {}
    for col in df_mk.columns:
        col_upper = str(col).strip().upper().replace(' ', '')
        if 'RUTPAC' in col_upper or col_upper == 'RUT':
            col_map[col] = 'RUTPACIENTE'
        elif 'FECHA' in col_upper and 'ADM' not in col_upper and 'ALTA' not in col_upper and 'MOV' not in col_upper:
            if 'FECHA_MK' not in col_map.values():
                col_map[col] = 'FECHA_MK'
        elif 'NOMAGRUP' in col_upper or 'GRUPO' in col_upper:
            col_map[col] = 'NOMAGRUP'
        elif 'NOMARTICULO' in col_upper or 'ARTICULO' in col_upper or 'NOMBREARTICULO' in col_upper:
            col_map[col] = 'NOMARTICULO'
        elif col_upper in ('TOTAL', 'MONTOTOTAL', 'TOTALITEM'):
            col_map[col] = 'TOTAL'
        elif 'COSTO' in col_upper:
            if 'COSTO' not in col_map.values():
                col_map[col] = 'COSTO'

    df_mk = df_mk.rename(columns=col_map)

    cols_requeridas = ['RUTPACIENTE', 'FECHA_MK']
    faltantes = [c for c in cols_requeridas if c not in df_mk.columns]
    if faltantes:
        print(f"   Columnas no encontradas en MK: {faltantes}")
        usar_factor_fijo = True
    else:
        df_mk['RUT_limpio'] = (
            df_mk['RUTPACIENTE']
            .astype(str)
            .str.replace(r'[.\s]', '', regex=True)
            .str.strip()
            .str.upper()
        )

        df_mk['FECHA_MK'] = pd.to_datetime(df_mk['FECHA_MK'], errors='coerce', dayfirst=True)
        df_mk = df_mk[df_mk['FECHA_MK'].notna()]
        df_mk['Fecha_date_mk'] = df_mk['FECHA_MK'].dt.date

        if 'NOMAGRUP' in df_mk.columns:
            grupos_imagenologia = [
                'EXAMENES RAYOS', 'SCANNER', 'RESONANCIA MAGNETICA', 'MAMOGRAFIAS', 'MEDICINA NUCLEAR'
            ]
            df_mk = df_mk[df_mk['NOMAGRUP'].isin(grupos_imagenologia)]

        if 'TOTAL' in df_mk.columns:
            df_mk['TOTAL_num'] = pd.to_numeric(
                df_mk['TOTAL'].astype(str).str.replace(r'[^\d.-]', '', regex=True),
                errors='coerce'
            ).abs()

#  4. JOIN CARESTREAM × MK 
if not usar_factor_fijo:
    print("\n Ejecutando join Carestream × MK (RUT + Fecha ±1 día)...")

    cols_agg = {'n_prestaciones': ('RUT_limpio', 'count')}
    if 'TOTAL_num' in df_mk.columns: cols_agg['monto_total_mk'] = ('TOTAL_num', 'sum')
    if 'NOMAGRUP' in df_mk.columns: cols_agg['grupo_mk_moda'] = ('NOMAGRUP', lambda x: x.mode()[0] if len(x) > 0 else None)
    if 'NOMARTICULO' in df_mk.columns: cols_agg['articulo_mk_lista'] = ('NOMARTICULO', lambda x: ' | '.join(x.dropna().unique()[:3]))

    df_mk_agg = df_mk.groupby(['RUT_limpio', 'Fecha_date_mk']).agg(**cols_agg).reset_index()

    resultados_join = []
    cs_con_match = set()
    mk_index = {}
    
    for _, row in df_mk_agg.iterrows():
        rut = row['RUT_limpio']
        if rut not in mk_index: mk_index[rut] = []
        mk_index[rut].append(row)

    for idx, cs_row in df_cs.iterrows():
        rut = cs_row['RUT_limpio']
        fecha_cs = cs_row['Fecha_date']

        if rut not in mk_index: continue

        mejor_match = None
        menor_diff = float('inf')

        for mk_row in mk_index[rut]:
            diff = abs((mk_row['Fecha_date_mk'] - fecha_cs).days)
            if diff <= TOLERANCIA_DIAS and diff < menor_diff:
                menor_diff = diff
                mejor_match = mk_row

        if mejor_match is not None:
            resultados_join.append({
                'idx_cs':          idx,
                'n_prestaciones':  mejor_match['n_prestaciones'],
                'dias_diferencia': menor_diff,
                **{k: mejor_match[k] for k in mejor_match.index if k not in ['RUT_limpio', 'Fecha_date_mk', 'n_prestaciones']},
            })
            cs_con_match.add(idx)

    df_join_resultados = pd.DataFrame(resultados_join).set_index('idx_cs') if resultados_join else pd.DataFrame()

    if len(cs_con_match) > 0:
        df_cs = df_cs.join(df_join_resultados, how='left')
    else:
        print("   Sin matches — verifica que los RUTs tengan el mismo formato en ambas bases")
        usar_factor_fijo = True

#  5. CALCULAR FACTOR DE AGRUPACIÓN 
print("\nCalculando Factor de Agrupación...")

if usar_factor_fijo or 'n_prestaciones' not in df_cs.columns:
    factores_por_modalidad_estimados = {
        'MR': 1.45, 'CT': 1.50, 'DX': 1.10, 'MG': 1.15, 'BM': 1.20,
        'NM': 1.25, 'CR': 1.10, 'OT': 1.20, 'PT': 1.30, 'RF': 1.10,
    }
    df_cs['n_prestaciones_est'] = df_cs['Mod'].map(factores_por_modalidad_estimados).fillna(FACTOR_FIJO)
    df_cs['n_prestaciones'] = df_cs['n_prestaciones_est']

    df_factor = pd.DataFrame([
        {'Modalidad': mod, 'Factor_Agrupacion': factor, 'n_examenes_cs': len(df_cs[df_cs['Mod']==mod]), 'Fuente': 'Estimado (sin MK)'}
        for mod, factor in factores_por_modalidad_estimados.items()
    ])
    factor_global = FACTOR_FIJO

else:
    df_con_match = df_cs[df_cs['n_prestaciones'].notna()].copy()
    df_con_match['n_prestaciones'] = pd.to_numeric(df_con_match['n_prestaciones'], errors='coerce')
    factor_global = df_con_match['n_prestaciones'].mean()

    df_factor = (
        df_con_match.groupby('Mod').agg(
            n_examenes_cs=('n_prestaciones', 'count'),
            Factor_Agrupacion=('n_prestaciones', 'mean')
        ).reset_index().rename(columns={'Mod': 'Modalidad'})
    )
    df_factor = df_factor[df_factor['n_examenes_cs'] >= 30].copy()
    df_factor['Factor_Agrupacion'] = df_factor['Factor_Agrupacion'].round(3)
    df_factor['Fuente'] = 'Real (join CS×MK)'

#  6. ENRIQUECER CON COMPLEJIDAD 
df_cs['Rango_Complejidad'] = pd.cut(
    pd.to_numeric(df_cs['n_prestaciones'], errors='coerce').fillna(1),
    bins=N_PRESTACIONES_BINS, labels=N_PRESTACIONES_LABELS, right=True
).astype(str).replace('nan', '1 prest.')

#  7. TABLA DE TARIFAS 
if not usar_factor_fijo and 'monto_total_mk' in df_cs.columns:
    df_cs['monto_total_mk'] = pd.to_numeric(df_cs['monto_total_mk'], errors='coerce')
    df_tarifas = df_cs[df_cs['monto_total_mk'].notna()].groupby('Mod').agg(Tarifa_Promedio=('monto_total_mk', 'mean')).reset_index().rename(columns={'Mod': 'Modalidad'})
    df_tarifas['Tarifa_Promedio'] = df_tarifas['Tarifa_Promedio'].round(0).astype(int)
else:
    tarifas_ref = {'MR': 850000, 'CT': 650000, 'DX': 35000, 'MG': 120000, 'BM': 180000, 'NM': 250000, 'CR': 30000,  'OT': 80000,  'PT': 500000}
    df_tarifas = pd.DataFrame([{'Modalidad': m, 'Tarifa_Promedio': t} for m, t in tarifas_ref.items()])

#  8. TABLA PUENTE PARA POWER BI 
df_puente_pbi = df_factor[['Modalidad', 'Factor_Agrupacion', 'n_examenes_cs', 'Fuente']].copy()
df_puente_pbi = df_puente_pbi.merge(df_tarifas[['Modalidad', 'Tarifa_Promedio']], on='Modalidad', how='left')

# AQUÍ ESTÁ LA FILA GLOBAL QUE FALTABA
global_row = pd.DataFrame([{
    'Modalidad': 'GLOBAL',
    'Factor_Agrupacion': round(factor_global, 3),
    'n_examenes_cs': len(df_cs),
    'Fuente': 'Calculado',
    'Tarifa_Promedio': df_tarifas['Tarifa_Promedio'].mean().round(0) if len(df_tarifas) > 0 else 0
}])
df_puente_pbi = pd.concat([df_puente_pbi, global_row], ignore_index=True)

# ── 9. EXPORTAR ───────────────────────────────────────────────────────
os.makedirs(RUTA_PROCESADO, exist_ok=True)
os.makedirs(RUTA_OUTPUTS, exist_ok=True)

def limpiar_para_parquet(df):
    df = df.copy()
    for col in df.columns:
        if df[col].dtype == object:
            df[col] = df[col].astype(str).str.strip().replace({'nan': None, 'None': None})
    return df

df_puente_pbi.pipe(limpiar_para_parquet).to_parquet(os.path.join(RUTA_PROCESADO, 'factor_agrupacion.parquet'), index=False)
df_tarifas.pipe(limpiar_para_parquet).to_parquet(os.path.join(RUTA_PROCESADO, 'tarifas_mk.parquet'), index=False)
df_cs.pipe(limpiar_para_parquet).to_parquet(os.path.join(RUTA_PROCESADO, 'fact_examenes_enriquecido.parquet'), index=False)

print(f"\nFactor de Agrupación Global restaurado: {factor_global:.3f}")
print("Archivo factor_agrupacion.parquet corregido exitosamente.")