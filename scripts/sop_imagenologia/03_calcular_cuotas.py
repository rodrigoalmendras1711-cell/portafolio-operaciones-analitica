import logging
import os
import calendar
import datetime
from itertools import product
from pathlib import Path
from typing import Tuple, Dict, Any

import pandas as pd
import numpy as np
import duckdb


# CONFIGURACIÓN Y LOGGING
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# Parámetros Originales
MES_OBJETIVO = 3
ANIO_OBJETIVO = 2026
ALPHA_DECAIMIENTO = 0.85
GRUPOS_EXCLUIDOS = ['ECOTOMOGRAFIAS', 'ECOCARDIOGRAMAS']
FERIADOS_CL = [] # Mantenido vacío como en el original


# FUNCIONES DE EXTRACCIÓN Y CÁLCULO

def cargar_presupuesto_gerencial(ruta_presupuesto: Path) -> int:
    """Extrae la meta financiera neta (S&OP) descontando modalidades excluidas."""
    if not ruta_presupuesto.exists():
        logging.error(f"ERROR CRÍTICO: No se encontró presupuesto en {ruta_presupuesto}")
        return 0

    df_presup = pd.read_excel(ruta_presupuesto)
    df_mes = df_presup[(df_presup['Anio'] == ANIO_OBJETIVO) & (df_presup['Mes'] == MES_OBJETIVO)]

    if df_mes.empty:
        logging.error(f"ERROR: Bastián no ha ingresado presupuesto para {ANIO_OBJETIVO}-{MES_OBJETIVO:02d}.")
        return 0

    meta_bruta = df_mes['Meta_Prestaciones'].sum()
    df_neto = df_mes[~df_mes['Grupo_MK'].astype(str).str.upper().isin(GRUPOS_EXCLUIDOS)]
    meta_neta = int(df_neto['Meta_Prestaciones'].sum())

    logging.info(f"Presupuesto BRUTO (Con Ecos) : {int(meta_bruta):,} prestaciones")
    logging.info(f"Presupuesto NETO OPERATIVO : {meta_neta:,} prestaciones")
    
    return meta_neta

def obtener_factor_agrupacion(ruta_factor: Path) -> float:
    """Recupera el factor de conversión operativo-financiero (Lógica Original)."""
    if ruta_factor.exists():
        df_factor = pd.read_parquet(ruta_factor)
        # Filtro estricto como en el original
        factor = df_factor[df_factor['Modalidad'] == 'GLOBAL']['Factor_Agrupacion'].values[0]
        return float(factor)
    
    factor_fijo = 1.358
    logging.warning(f"Usando factor fijo {factor_fijo} (corre puente_sop.py para actualizarlo)")
    return factor_fijo

def extraer_produccion_historica(ruta_parquet: Path, ruta_doctores: Path) -> pd.DataFrame:
    """Extrae producción y filtra médicos activos."""
    if not ruta_parquet.exists():
        logging.error(f"Error: No se encuentra {ruta_parquet.name}. Corre etl_pipeline.py primero.")
        return pd.DataFrame()

    con = duckdb.connect()
    query = f"""
        SELECT
            "Informe firmado por"    AS doctor,
            "Mod"                    AS modalidad,
            Anio_Mes,
            CAST("Fecha de firma final" AS DATE) AS fecha_exacta,
            DAYOFWEEK("Fecha de firma final") AS dia_duckdb,
            COUNT(*)                 AS examenes_firmados
        FROM parquet_scan('{ruta_parquet}')
        WHERE Tipo_Firmante = 'INTERNO'
          AND Estado_SLA = 'Cumplido'
        GROUP BY 1, 2, 3, 4, 5
    """
    df_th = con.execute(query).df()
    con.close()

    if ruta_doctores.exists():
        df_activos = pd.read_excel(ruta_doctores)
        df_activos['Nombre_Oficial'] = df_activos['Nombre_Oficial'].fillna(df_activos['Usuario_Sistema'])
        mapa_doctores = dict(zip(df_activos['Usuario_Sistema'], df_activos['Nombre_Oficial']))
        
        df_th = df_th[df_th['doctor'].isin(mapa_doctores.keys())].copy()
        df_th['doctor'] = df_th['doctor'].map(mapa_doctores)
        df_th = df_th.groupby(['doctor', 'modalidad', 'Anio_Mes', 'dia_duckdb', 'fecha_exacta'], as_index=False)['examenes_firmados'].sum()
    else:
        logging.warning("ADVERTENCIA: No se encontró doctores_activos.xlsx. Se usarán TODOS los médicos históricos.")
        
    return df_th

def contar_dias_tipo_en_rango(dia_semana_py: int, str_anio_mes: str) -> float:
    """Lógica original de conteo de días."""
    if pd.isna(str_anio_mes): return 4.0 
    anio, mes = int(str_anio_mes[:4]), int(str_anio_mes[5:7])
    dias_en_mes = calendar.monthrange(anio, mes)[1]
    contador = 0
    for d in range(1, dias_en_mes + 1):
        fecha = datetime.date(anio, mes, d)
        if fecha.weekday() == dia_semana_py and fecha not in FERIADOS_CL:
            contador += 1
    return float(contador)

def proyectar_demanda_ml(ruta_parquet: Path) -> int:
    """Proyección ML (Expanding Window) original."""
    logging.info("Ejecutando Walk-Forward Cross Validation (Proyección ML)...")
    con = duckdb.connect()
    query = f"""
        SELECT 
            CAST(SUBSTRING(Anio_Mes, 1, 4) AS INT) as anio, 
            CAST(SUBSTRING(Anio_Mes, 6, 2) AS INT) as mes, 
            COUNT(*) as volumen 
        FROM parquet_scan('{ruta_parquet}') 
        GROUP BY 1, 2
    """
    df_trend = con.execute(query).df()
    con.close()

    def calcular_demanda(a_obj, m_obj):
        vol_mes_pasado = df_trend[(df_trend['anio'] == a_obj - 1) & (df_trend['mes'] == m_obj)]['volumen'].sum()
        f_crec = 1.0
        if m_obj > 1:
            y_act = df_trend[(df_trend['anio'] == a_obj) & (df_trend['mes'] < m_obj)]['volumen'].sum()
            y_hist = df_trend[(df_trend['anio'] == a_obj - 1) & (df_trend['mes'] < m_obj)]['volumen'].sum()
            if y_hist > 0: f_crec = y_act / y_hist
        else:
            v_a1 = df_trend[df_trend['anio'] == a_obj - 1]['volumen'].sum()
            v_a2 = df_trend[df_trend['anio'] == a_obj - 2]['volumen'].sum()
            if v_a2 > 0: f_crec = v_a1 / v_a2
        dem_tend = int(vol_mes_pasado * f_crec)
        
        m_ant = m_obj - 1 if m_obj > 1 else 12
        a_ant = a_obj if m_obj > 1 else a_obj - 1
        vars_hist = []
        for a_h in range(a_obj - 3, a_obj):
            a_h_ant = a_h if m_obj > 1 else a_h - 1
            v_m1 = df_trend[(df_trend['anio'] == a_h_ant) & (df_trend['mes'] == m_ant)]['volumen'].sum()
            v_m2 = df_trend[(df_trend['anio'] == a_h) & (df_trend['mes'] == m_obj)]['volumen'].sum()
            if v_m1 > 0: vars_hist.append(v_m2 / v_m1)
        var_prom = sum(vars_hist)/len(vars_hist) if vars_hist else 1.0
        v_act_ant = df_trend[(df_trend['anio'] == a_ant) & (df_trend['mes'] == m_ant)]['volumen'].sum()
        dem_est = int(v_act_ant * var_prom)
        return dem_est, dem_tend

    pesos_posibles = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
    menor_mae, mejores_pesos = float('inf'), (0.6, 0.4)
    meses_validacion = df_trend[df_trend['anio'] == ANIO_OBJETIVO - 1]
    
    for w_est, w_tend in product(pesos_posibles, repeat=2):
        if abs(w_est + w_tend - 1.0) > 0.01: continue 
        errores = []
        for _, row in meses_validacion.iterrows():
            mes_futuro, anio_futuro, realidad_oculta = int(row['mes']), int(row['anio']), row['volumen']
            est, tend = calcular_demanda(anio_futuro, mes_futuro)
            forecast_ciego = (w_est * est) + (w_tend * tend)
            errores.append(abs(forecast_ciego - realidad_oculta))
        
        if errores and np.mean(errores) < menor_mae:
            menor_mae, mejores_pesos = float(np.mean(errores)), (w_est, w_tend)

    w_est, w_tend = mejores_pesos
    dem_est, dem_tend = calcular_demanda(ANIO_OBJETIVO, MES_OBJETIVO)
    return int((dem_est * w_est) + (dem_tend * w_tend))


# ORQUESTADOR S&OP

def orquestar_s_and_op(dir_data: Path, dir_output: Path):
    logging.info(f"GESTIÓN DE CAPACIDAD Y COLAS (V3.0)")
    
    ruta_presupuesto = dir_data / 'raw/presupuestos/Presupuesto_Gerencia.xlsx'
    ruta_fact = dir_output / 'fact_examenes.parquet'
    ruta_factor = dir_output / 'factor_agrupacion.parquet'
    ruta_doctores = dir_data / 'doctores_activos.xlsx'
    
    meta_prestaciones_bastian = cargar_presupuesto_gerencial(ruta_presupuesto)
    if meta_prestaciones_bastian == 0: return

    factor_agrupacion = obtener_factor_agrupacion(ruta_factor)
    df_th = extraer_produccion_historica(ruta_fact, ruta_doctores)
    
    if df_th.empty: return

    # 2.5 Normalización
    df_agrupado = df_th.groupby(['doctor', 'modalidad', 'Anio_Mes', 'dia_duckdb']).agg(
        examenes_firmados=('examenes_firmados', 'sum'),
        dias_activos_reales=('fecha_exacta', 'nunique')
    ).reset_index()

    mapa_python = {1: 0, 2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 0: 6}
    df_agrupado['dia_py'] = df_agrupado['dia_duckdb'].map(mapa_python)
    df_agrupado['dias_habiles_calendario'] = df_agrupado.apply(lambda r: contar_dias_tipo_en_rango(r['dia_py'], r['Anio_Mes']), axis=1)
    df_agrupado['factor_presencia_real'] = (df_agrupado['dias_activos_reales'] / df_agrupado['dias_habiles_calendario'].clip(lower=1)).clip(upper=1.0)
    df_agrupado['velocidad_pura'] = df_agrupado['examenes_firmados'] / df_agrupado['dias_activos_reales'].clip(lower=1)
    df_agrupado['salida_esperada_real'] = df_agrupado['velocidad_pura'] * df_agrupado['factor_presencia_real']

    mapa_salida = {0: 1, 1: 2, 2: 3, 3: 4, 4: 5, 5: 6, 6: 7}
    df_agrupado['dia_salida'] = df_agrupado['dia_py'].map(mapa_salida)

    hoy = datetime.date.today()
    def calcular_peso(anio_mes):
        if pd.isna(anio_mes): return 0.0
        anio, mes = int(anio_mes[:4]), int(anio_mes[5:7])
        fecha_mes = datetime.date(anio, mes, 1)
        meses_antiguedad = (hoy.year - fecha_mes.year) * 12 + (hoy.month - fecha_mes.month)
        return ALPHA_DECAIMIENTO ** meses_antiguedad if meses_antiguedad >= 0 else 1.0

    df_agrupado['peso'] = df_agrupado['Anio_Mes'].apply(calcular_peso)
    df_agrupado['salida_ponderada'] = df_agrupado['salida_esperada_real'] * df_agrupado['peso']

    salida_doctor_dia = df_agrupado.groupby(['doctor', 'modalidad', 'dia_salida']).apply(
        lambda g: g['salida_ponderada'].sum() / g['peso'].sum() if g['peso'].sum() > 0 else 0
    ).reset_index(name='capacidad_salida')
    
    dict_salida = salida_doctor_dia.set_index(['doctor', 'modalidad', 'dia_salida'])['capacidad_salida'].to_dict()

    # 4. Traducir a cuotas
    doctores_mods = df_agrupado[['doctor', 'modalidad']].drop_duplicates().values
    datos_entrada = []
    
    for doc, mod in doctores_mods:
        def get_salida_hist(dias):
            vals = [dict_salida.get((doc, mod, d), None) for d in dias]
            vals_validos = [v for v in vals if v is not None]
            return sum(vals_validos) if vals_validos else None
        
        datos_entrada.extend([
            {'doctor': doc, 'modalidad': mod, 'dia_asignacion': 1, 'nombre_dia': 'lunes',     'cuota_entrada': get_salida_hist([3])},
            {'doctor': doc, 'modalidad': mod, 'dia_asignacion': 2, 'nombre_dia': 'martes',    'cuota_entrada': get_salida_hist([4])},
            {'doctor': doc, 'modalidad': mod, 'dia_asignacion': 3, 'nombre_dia': 'miércoles', 'cuota_entrada': get_salida_hist([5])},
            {'doctor': doc, 'modalidad': mod, 'dia_asignacion': 4, 'nombre_dia': 'jueves',    'cuota_entrada': get_salida_hist([1, 6, 7])},
            {'doctor': doc, 'modalidad': mod, 'dia_asignacion': 5, 'nombre_dia': 'viernes',   'cuota_entrada': get_salida_hist([2])}
        ])

    df_entrada = pd.DataFrame(datos_entrada)
    df_entrada['cuota_entrada'] = df_entrada['cuota_entrada'].apply(lambda x: int(round(x)) if pd.notna(x) else x)

    # 5. Calendario
    dias_mes = calendar.monthrange(ANIO_OBJETIVO, MES_OBJETIVO)[1]
    conteo_dias_habiles = {1:0, 2:0, 3:0, 4:0, 5:0}
    for dia in range(1, dias_mes + 1):
        f_actual = datetime.date(ANIO_OBJETIVO, MES_OBJETIVO, dia)
        if f_actual.weekday() < 5 and f_actual not in FERIADOS_CL:
            conteo_dias_habiles[f_actual.weekday() + 1] += 1

    total_habiles = sum(conteo_dias_habiles.values())
    df_entrada['dias_en_mes'] = df_entrada['dia_asignacion'].map(conteo_dias_habiles)
    df_entrada['capacidad_mensual'] = df_entrada['cuota_entrada'].fillna(0) * df_entrada['dias_en_mes']
    capacidad_total_mes = df_entrada['capacidad_mensual'].sum()

    # 5.5 Capacidad Anual
    logging.info(f"Calculando Capacidad Dinámica para todos los meses de {ANIO_OBJETIVO}...")
    datos_cap_anual = []
    for mes in range(1, 13):
        dias_del_mes = calendar.monthrange(ANIO_OBJETIVO, mes)[1]
        conteo_dias_mes = {1:0, 2:0, 3:0, 4:0, 5:0}
        for dia in range(1, dias_del_mes + 1):
            f_act = datetime.date(ANIO_OBJETIVO, mes, dia)
            if f_act.weekday() < 5 and f_act not in FERIADOS_CL:
                conteo_dias_mes[f_act.weekday() + 1] += 1
                
        for _, row in df_entrada.iterrows():
            if pd.notna(row['cuota_entrada']):
                cap_mensual = row['cuota_entrada'] * conteo_dias_mes[row['dia_asignacion']]
                datos_cap_anual.append({'Anio_Mes': f"{ANIO_OBJETIVO}-{mes:02d}", 'Modalidad': row['modalidad'], 'Capacidad_Proyectada': cap_mensual})

    df_capacidad_anual = pd.DataFrame(datos_cap_anual).groupby(['Anio_Mes', 'Modalidad'])['Capacidad_Proyectada'].sum().reset_index()
    ruta_capacidad_anual = dir_output / 'capacidad_anual_2026.parquet'
    df_capacidad_anual.to_parquet(ruta_capacidad_anual, index=False)

    # 6. Demanda
    demanda_proyectada = proyectar_demanda_ml(ruta_fact)
    meta_informes_cs = int(meta_prestaciones_bastian / factor_agrupacion)
    
    brecha_itms_bastian = max(meta_informes_cs - capacidad_total_mes, 0)
    brecha_itms_real    = max(demanda_proyectada - capacidad_total_mes, 0)
    rho_bastian = meta_informes_cs / capacidad_total_mes if capacidad_total_mes > 0 else float('inf')
    rho_real    = demanda_proyectada / capacidad_total_mes if capacidad_total_mes > 0 else float('inf')

    logging.info(f"Capacidad Interna Médica : {capacidad_total_mes:,.0f} informes")
    logging.info(f"Meta Finanzas (Bastián)  : {meta_informes_cs:,.0f} informes")
    logging.info(f"Demanda Real (ML)        : {demanda_proyectada:,.0f} informes")
    logging.info(f"Brecha Mínima -> ITMS    : {brecha_itms_bastian:,.0f} informes")
    logging.info(f"Brecha Real -> ITMS      : {brecha_itms_real:,.0f} informes")

    if rho_real > 0.85:
        logging.warning("ALERTA CRÍTICA: Sistema inestable (ρ > 0.85). Riesgo Ley de Kingman.")

    # 8. Exportación
    ruta_informe = dir_output / f'Matriz_Cuotas_{ANIO_OBJETIVO}_{MES_OBJETIVO:02d}.xlsx'
    
    df_resumen = pd.DataFrame({
        'Métrica': ['Días Hábiles del Mes', 'Capacidad Interna Máxima (Informes)', 'Meta Financiera Bastián (Informes CS)', 'Demanda Operativa Proyectada (Informes CS)', 'Brecha a Derivar a ITMS (Según Ppto Bastián)', 'Brecha a Derivar a ITMS (Para evitar Colapso)'],
        'Total Mes': [total_habiles, capacidad_total_mes, meta_informes_cs, demanda_proyectada, brecha_itms_bastian, brecha_itms_real]
    })

    df_pivot = df_entrada.pivot_table(index=['doctor', 'modalidad'], columns='nombre_dia', values='cuota_entrada', aggfunc='first')
    df_pivot = df_pivot[['lunes', 'martes', 'miércoles', 'jueves', 'viernes']]
    df_pivot['Total'] = df_pivot.sum(axis=1, min_count=1)

    df_totales = df_entrada.groupby(['doctor', 'nombre_dia'])['cuota_entrada'].sum(min_count=1).reset_index()
    df_totales['modalidad'] = 'Total'
    df_tot_pivot = df_totales.pivot_table(index=['doctor', 'modalidad'], columns='nombre_dia', values='cuota_entrada', aggfunc='first')
    df_tot_pivot = df_tot_pivot[['lunes', 'martes', 'miércoles', 'jueves', 'viernes']]
    df_tot_pivot['Total'] = df_tot_pivot.sum(axis=1, min_count=1)

    df_final_excel = pd.concat([df_tot_pivot, df_pivot]).reset_index()
    df_final_excel['sort_mod'] = df_final_excel['modalidad'].apply(lambda x: 0 if x == 'Total' else 1)
    df_final_excel = df_final_excel.sort_values(['doctor', 'sort_mod', 'modalidad']).drop(columns=['sort_mod'])
    df_final_excel.rename(columns={'doctor': 'Informe firmado por', 'modalidad': 'Modalidad'}, inplace=True)
    
    for c in ['lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'Total']:
        df_final_excel[c] = df_final_excel[c].astype(object)
        df_final_excel[c] = df_final_excel[c].apply(lambda x: '-' if pd.isna(x) else int(x))

    with pd.ExcelWriter(ruta_informe, engine='openpyxl') as writer:
        df_resumen.to_excel(writer, sheet_name='1_SOP_Gerencial', index=False, startrow=0)
        df_final_excel.to_excel(writer, sheet_name='2_Matriz_Operativa', index=False)
        
    df_entrada.to_parquet(dir_output / 'matriz_cuotas_integrada.parquet', index=False)
    logging.info(f"Matriz de cuotas guardada en: {ruta_informe.name}")

if __name__ == "__main__":
    BASE_DIR = Path(__file__).resolve().parent.parent.parent
    orquestar_s_and_op(
        dir_data=BASE_DIR / 'data/sop_imagenologia',
        dir_output=BASE_DIR / 'output/sop_imagenologia'
    )