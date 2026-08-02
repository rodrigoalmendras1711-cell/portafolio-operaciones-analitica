import logging
import datetime
from pathlib import Path
from typing import Dict, Tuple, Optional

import pandas as pd
import numpy as np
import duckdb


# CONFIGURACIÓN GENERAL
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# Parámetros del ciclo S&OP
ANIO_OBJETIVO = 2026
MES_OBJETIVO = 3
MESES_MINIMO_HISTORIA = 6
ALPHA_DECAIMIENTO = 0.85

MAPA_BASTIAN_A_CS = {
    "DENSITOMETRIA": "BM",
    "ECOCARDIOGRAMAS": None,  # Excluido
    "ECOTOMOGRAFIAS": None,   # Excluido
    "MAMOGRAFIAS": "MG",
    "EXAMENES RAYOS": "DX",
    "RESONANCIA MAGNETICA": "MR",
    "SCANNER": "CT",
}


# EXTRACCIÓN DE PARÁMETROS (FINANZAS Y OPERACIONES)

def cargar_presupuesto_bastian(ruta_excel: Path) -> Tuple[Dict, int, Dict, Dict, int, int]:
    """Lee las metas comerciales y las traduce al lenguaje operacional de Carestream."""
    if not ruta_excel.exists():
        logging.error(f"Falta el presupuesto en {ruta_excel.name}")
        return {}, 0, {}, {}, 0, 0

    df_presup = pd.read_excel(ruta_excel)
    df_mes = df_presup[(df_presup['Anio'] == ANIO_OBJETIVO) & (df_presup['Mes'] == MES_OBJETIVO)]
    
    if df_mes.empty:
        logging.error("No hay presupuesto cargado para el mes objetivo.")
        return {}, 0, {}, {}, 0, 0

    ppto_bruto = dict(zip(df_mes['Grupo_MK'], df_mes['Meta_Prestaciones']))
    total_bruto = sum(ppto_bruto.values())
    
    return ppto_bruto, total_bruto

def obtener_factores_agrupacion(ruta_factor: Path) -> Tuple[float, Dict]:
    """Obtiene los factores de conversión de informes a prestaciones"""
    if ruta_factor.exists():
        df = pd.read_parquet(ruta_factor)
        factor_global = float(df[df['Modalidad'] == 'GLOBAL']['Factor_Agrupacion'].values[0])
        factor_x_mod = df[df['Modalidad'] != 'GLOBAL'].set_index('Modalidad')['Factor_Agrupacion'].to_dict()
        return factor_global, factor_x_mod
    
    logging.warning("Usando factores fijos (Fallback). Ejecuta puente_sop.py.")
    return 1.718, {'CT': 1.861, 'DX': 1.863, 'MR': 1.553, 'MG': 1.097, 'BM': 1.643}

def leer_resultados_forecasting(ruta_cuotas_excel: Path) -> Tuple[Optional[int], Optional[int], Optional[int]]:
    """Extrae las proyecciones calculadas en el Script 3."""
    if not ruta_cuotas_excel.exists():
        logging.error(f"No se encontró {ruta_cuotas_excel.name}")
        return None, None, None

    df_sop = pd.read_excel(ruta_cuotas_excel, sheet_name='1_SOP_Gerencial', nrows=7)
    df_sop.columns = ['Metrica', 'Total_Mes']

    def extraer(nombre):
        fila = df_sop[df_sop['Metrica'].str.strip() == nombre]
        return int(fila['Total_Mes'].values[0]) if not fila.empty else None

    demanda_cs = extraer('Demanda Operativa Proyectada (Informes CS)')
    capacidad = extraer('Capacidad Interna Máxima (Informes)')
    dias = extraer('Días Hábiles del Mes')
    
    return demanda_cs, capacidad, dias


# ANÁLISIS DE SATURACIÓN Y COLAS

def procesar_saturacion_y_backlog(
    rutas: Dict[str, Path], 
    factor_global: float, 
    factor_x_mod: Dict,
    demanda_forecast_cs: int,
    cap_interna: int,
    ppto_bruto: Dict
):
    """Calcula el índice rho, brechas y backlog histórico integrando todas las fuentes."""
    # 1. Calcular Presupuesto Neto a Informes
    prest_x_mod_bastian, informes_x_mod_bastian = {}, {}
    excluidas_prest = 0

    for cat, prest in ppto_bruto.items():
        mod_cs = MAPA_BASTIAN_A_CS.get(cat)
        if mod_cs is None:
            excluidas_prest += prest
        else:
            fact = factor_x_mod.get(mod_cs, factor_global)
            informes = round(prest / fact)
            prest_x_mod_bastian[mod_cs] = prest_x_mod_bastian.get(mod_cs, 0) + prest
            informes_x_mod_bastian[mod_cs] = informes_x_mod_bastian.get(mod_cs, 0) + informes

    meta_informes_bastian = sum(informes_x_mod_bastian.values())

    # 2. Consultas Datalake (DuckDB)
    con = duckdb.connect()
    con.execute(f"CREATE VIEW fact AS SELECT * FROM parquet_scan('{rutas['fact']}')")
    
    df_hist = con.execute("""
        SELECT CAST(SUBSTRING(Anio_Mes, 1, 4) AS INT) AS anio,
               CAST(SUBSTRING(Anio_Mes, 6, 2) AS INT) AS mes,
               Anio_Mes, COUNT(*) AS volumen_total
        FROM fact GROUP BY 1,2,3 ORDER BY 1,2
    """).df()

    df_hist_mod = con.execute("""
        SELECT CAST(SUBSTRING(Anio_Mes, 1, 4) AS INT) AS anio,
               CAST(SUBSTRING(Anio_Mes, 6, 2) AS INT) AS mes,
               Anio_Mes, Mod AS modalidad, COUNT(*) AS volumen
        FROM fact GROUP BY 1,2,3,4 ORDER BY 1,2,4
    """).df()

    df_bl_raw = con.execute("""
        SELECT Anio_Mes AS mes_toma,
               CAST(SUBSTRING(CAST("Fecha de firma final" AS VARCHAR),1,7) AS VARCHAR) AS mes_firma,
               COUNT(*) AS examenes
        FROM fact
        WHERE "Fecha de firma final" IS NOT NULL AND Tipo_Firmante = 'INTERNO'
        GROUP BY 1,2 ORDER BY 1,2
    """).df()
    con.close()

    # 3. Modelado de Mix Ponderado y Tendencias
    mods_validas = df_hist_mod.groupby("modalidad")["Anio_Mes"].nunique().loc[lambda x: x >= MESES_MINIMO_HISTORIA].index.tolist()
    df_hist_mod = df_hist_mod[df_hist_mod["modalidad"].isin(mods_validas)]

    df_cuotas = pd.read_parquet(rutas['cuotas_pq'])
    cap_x_mod = df_cuotas.groupby("modalidad")["capacidad_mensual"].sum().reset_index().rename(columns={"modalidad": "Modalidad", "capacidad_mensual": "Cap_Mensual_Interna"})

    hoy = datetime.date.today()
    df_mix = df_hist_mod.merge(df_hist[["anio","mes","volumen_total"]], on=["anio","mes"])
    df_mix["pct_mix"] = df_mix["volumen"] / df_mix["volumen_total"]
    df_mix["peso"] = df_mix.apply(lambda r: ALPHA_DECAIMIENTO ** max((hoy.year - int(r["anio"])) * 12 + (hoy.month - int(r["mes"])), 0), axis=1)
    
    mix_ponderado = df_mix.groupby("modalidad").apply(lambda g: (g["pct_mix"] * g["peso"]).sum() / g["peso"].sum() if g["peso"].sum() > 0 else 0).reset_index(name="Mix_Ponderado").rename(columns={"modalidad": "Modalidad"})

    tendencias = df_hist_mod.groupby("modalidad").apply(lambda g: pd.Series({
        "vol_2024": int(g[g["anio"]==2024]["volumen"].sum()),
        "vol_2025": int(g[g["anio"]==2025]["volumen"].sum()),
    })).reset_index().rename(columns={"modalidad": "Modalidad"})
    tendencias["Tendencia_Anual"] = (tendencias["vol_2025"] / tendencias["vol_2024"].clip(lower=1)).round(3)

    # 4. Cálculo de Rho y Ensamblaje Final
    df_rho = mix_ponderado.merge(cap_x_mod, on="Modalidad", how="left").merge(tendencias[["Modalidad","Tendencia_Anual","vol_2025"]], on="Modalidad", how="left").rename(columns={"vol_2025": "Vol_Real_2025"})

    df_rho["Demanda_Forecast_CS"] = (df_rho["Mix_Ponderado"] * demanda_forecast_cs).round(0).astype(int)
    df_rho["Demanda_Forecast_MK"] = (df_rho["Demanda_Forecast_CS"] * factor_global).round(0).astype(int)
    df_rho["Prest_Bastian"] = df_rho["Modalidad"].map(prest_x_mod_bastian).fillna(0).astype(int)
    df_rho["Informes_Bastian"] = df_rho["Modalidad"].map(informes_x_mod_bastian).fillna(0).astype(int)

    df_rho["rho_forecast"] = (df_rho["Demanda_Forecast_CS"] / df_rho["Cap_Mensual_Interna"].clip(lower=1)).round(3)
    df_rho["rho_bastian"] = (df_rho["Informes_Bastian"] / df_rho["Cap_Mensual_Interna"].clip(lower=1)).round(3)

    def diagnostico(r):
        if pd.isna(r): return "— Sin datos"
        if r > 1.0: return " COLAPSO"
        if r > 0.85: return " CRÍTICO"
        if r > 0.70: return " ALTO"
        return " ESTABLE"

    df_rho["Diag_Forecast"] = df_rho["rho_forecast"].apply(diagnostico)
    df_rho["Diag_Bastian"] = df_rho["rho_bastian"].apply(diagnostico)
    df_rho["Brecha_Forecast_Inf"] = (df_rho["Demanda_Forecast_CS"] - df_rho["Cap_Mensual_Interna"]).clip(lower=0).round(0).astype(int)
    df_rho["Brecha_Bastian_Inf"] = (df_rho["Informes_Bastian"] - df_rho["Cap_Mensual_Interna"]).clip(lower=0).round(0).astype(int)
    df_rho["Brecha_Forecast_MK"] = (df_rho["Brecha_Forecast_Inf"] * factor_global).round(0).astype(int)
    df_rho["Brecha_Bastian_MK"] = (df_rho["Brecha_Bastian_Inf"] * factor_global).round(0).astype(int)
    df_rho["Mix_Pct"] = (df_rho["Mix_Ponderado"] * 100).round(1)

    df_rho = df_rho.sort_values("rho_forecast", ascending=False).reset_index(drop=True)

    # 5. Exportaciones
    df_rho_pbi = df_rho.copy()
    for col in df_rho_pbi.select_dtypes(include=["object","str"]).columns:
        df_rho_pbi[col] = df_rho_pbi[col].astype(str).str.strip()
    df_rho_pbi.to_parquet(rutas['out_parquet'], index=False)

    with pd.ExcelWriter(rutas['out_excel'], engine="openpyxl") as writer:
        df_rho.to_excel(writer, sheet_name="Saturacion_x_Modalidad", index=False)

    # Imprimir Reporte Directivo
    rho_global = demanda_forecast_cs / cap_interna if cap_interna > 0 else float('inf')
    brecha = max(demanda_forecast_cs - cap_interna, 0)
    
    logging.info("REPORTE EJECUTIVO DE SATURACIÓN")
    logging.info(f"Demanda Real Proyectada : {demanda_forecast_cs:,} informes")
    logging.info(f"Presupuesto Jefatura    : {meta_informes_bastian:,} informes")
    logging.info(f"Demanda Oculta (Delta)  : {demanda_forecast_cs - meta_informes_bastian:,} informes que colapsarán la cola")
    logging.info(f"Capacidad Interna Máxima: {cap_interna:,} informes")
    logging.info(f"Brecha a derivar a ITMS : {brecha:,} informes")
    
    if rho_global > 0.85:
        logging.warning(f"LEY DE KINGMAN: Sistema inestable (ρ={rho_global:.2f}). Se exige derivación preventiva a ITMS.")


# ORQUESTADOR

def main():
    base_dir = Path(__file__).resolve().parent.parent.parent
    
    rutas = {
        'ppto': base_dir / 'data/sop_imagenologia/raw/presupuestos/Presupuesto_Gerencia.xlsx',
        'factor': base_dir / 'output/sop_imagenologia/factor_agrupacion.parquet',
        'cuotas_ex': base_dir / f'output/sop_imagenologia/Matriz_Cuotas_{ANIO_OBJETIVO}_{MES_OBJETIVO:02d}.xlsx',
        'cuotas_pq': base_dir / 'output/sop_imagenologia/matriz_cuotas_integrada.parquet',
        'fact': base_dir / 'output/sop_imagenologia/fact_examenes.parquet',
        'out_excel': base_dir / 'output/sop_imagenologia/Analisis_Saturacion.xlsx',
        'out_parquet': base_dir / 'output/sop_imagenologia/saturacion_modalidad.parquet'
    }

    ppto_bruto, _ = cargar_presupuesto_bastian(rutas['ppto'])
    if not ppto_bruto: return

    factor_global, factor_x_mod = obtener_factores_agrupacion(rutas['factor'])
    demanda_cs, capacidad, _ = leer_resultados_forecasting(rutas['cuotas_ex'])
    
    if demanda_cs is None: return

    procesar_saturacion_y_backlog(
        rutas, factor_global, factor_x_mod, demanda_cs, capacidad, ppto_bruto
    )

if __name__ == "__main__":
    main()